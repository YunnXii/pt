import time

from app.box_config import load_box_config, load_box_state, save_box_state
from app.box_decision import add_observation, evaluate_torrent, watch_retry_delay
from app.box_mteam import (
    BoxApiBudgetError,
    box_api_budget_snapshot,
    box_torrent_bytes,
    box_torrent_detail,
)
from app.box_service import _safe_filename, disk_snapshot, traffic_snapshot
from app.box_service_v2 import _decision_row
from app.box_service_v3 import BoxControllerV3
from app.mteam import client as mteam_client
from app.qbittorrent import QBittorrentClient
from app.store import append_download_log


def _queue_sort_key(item):
    tid, row = item
    return (
        float(row.get("priority") or 0),
        float(row.get("score") or 0),
        -float(row.get("size_gb") or 0),
        -int(row.get("first_wait_at") or 0),
        str(tid),
    )


def _latest_wait_resource_rows(decisions: list) -> dict:
    """从决策历史恢复每个 torrent 的“最新状态”；只有最新状态仍是 wait-resource 才入队。"""
    latest = {}
    for row in reversed(decisions or []):
        if not isinstance(row, dict):
            continue
        tid = str(row.get("torrent_id") or "")
        if not tid or tid in latest:
            continue
        latest[tid] = row
    return {
        tid: row
        for tid, row in latest.items()
        if str(row.get("result") or "") == "wait-resource"
    }


class BoxControllerV4(BoxControllerV3):
    """V3 + 持久化资源等待队列。

    wait-resource 不再依赖 RSS 继续保留该项目；每轮先 cleanup，再优先复查等待队列，
    确认需求仍然成立且空间/下载槽已经释放后再下载。
    """

    def _sync_resource_queue_from_decisions(self, state: dict, cfg: dict, now_ts: int) -> int:
        queue = dict(state.get("resource_wait_queue") or {})
        seen = set(str(x) for x in (state.get("seen_ids") or []))
        retry_at = dict(state.get("watch_retry_at") or {})
        recheck = max(20, int(cfg.get("resource_queue_recheck_seconds") or 60))
        max_items = max(10, min(500, int(cfg.get("resource_queue_max_items") or 120)))
        added = 0

        latest = _latest_wait_resource_rows(state.get("decisions") or [])
        for tid, row in latest.items():
            if tid in seen:
                queue.pop(tid, None)
                continue
            old = queue.get(tid) if isinstance(queue.get(tid), dict) else {}
            first_wait = int(old.get("first_wait_at") or now_ts)
            # 旧 wait-resource 第一次迁移进独立队列时必须“立即可复查”。
            # 否则同一轮 drain 会因为 next_retry_at=now+60 而跳过，随后 RSS 又被
            # watch_retry_at 退避，用户只会看到一屏“退避跳过”。已有队列项则保留
            # 自己的下一次复查时间，最多不会被推迟超过一个 recheck 周期。
            if old:
                next_retry_at = min(
                    int(old.get("next_retry_at") or now_ts),
                    now_ts + recheck,
                )
            else:
                next_retry_at = now_ts
            queue[tid] = {
                "name": str(row.get("name") or old.get("name") or tid)[:180],
                "first_wait_at": first_wait,
                "last_wait_at": int(old.get("last_wait_at") or now_ts),
                "next_retry_at": next_retry_at,
                "score": float(row.get("score") or old.get("score") or 0),
                "priority": float(row.get("priority") or old.get("priority") or 0),
                "size_gb": float(row.get("size_gb") or old.get("size_gb") or 0),
                "reason": str(row.get("reason") or old.get("reason") or "资源不足")[:300],
            }
            # 主 RSS 暂时不要重复处理这只种；独立资源队列不受这个时间限制。
            retry_at[tid] = max(int(retry_at.get(tid) or 0), now_ts + recheck)
            if not old:
                added += 1

        # 如果某个 torrent 的最新决策已经不是 wait-resource，则不要因为更老的记录重新塞回来。
        latest_ids = set(latest)
        latest_all = {}
        for row in reversed(state.get("decisions") or []):
            if not isinstance(row, dict):
                continue
            tid = str(row.get("torrent_id") or "")
            if tid and tid not in latest_all:
                latest_all[tid] = str(row.get("result") or "")
        for tid in list(queue):
            if tid in seen:
                queue.pop(tid, None)
                retry_at.pop(tid, None)
                continue
            result = latest_all.get(tid)
            if result and result not in ("wait-resource", "resource-recheck-watch") and tid not in latest_ids:
                queue.pop(tid, None)

        if len(queue) > max_items:
            ranked = sorted(queue.items(), key=_queue_sort_key, reverse=True)[:max_items]
            queue = dict(ranked)

        state["resource_wait_queue"] = queue
        state["watch_retry_at"] = retry_at
        return added

    @staticmethod
    def _rough_resource_fit(row: dict, cfg: dict, qitems: list, traffic: dict, disk: dict) -> tuple:
        if traffic.get("budget_reached"):
            return False, "月流量软预算已到"
        summary = QBittorrentClient.summary(qitems)
        max_active = max(1, int(cfg.get("max_active_downloads") or 1))
        if int(summary.get("active_downloads") or 0) >= max_active:
            return False, "下载槽仍被占用"
        size_gb = max(0.0, float(row.get("size_gb") or 0))
        data_cap = float(cfg.get("data_cap_gb") or 0)
        if data_cap > 0 and size_gb > 0:
            current_gb = float(summary.get("total_size") or 0) / (1024 ** 3)
            if current_gb + size_gb > data_cap:
                return False, "盒子数据上限仍不足"
        reserve = float(cfg.get("disk_reserve_gb") or 0)
        if size_gb > 0 and float(disk.get("free_gb") or 0) - size_gb < reserve:
            return False, "磁盘安全预留仍不足"
        return True, ""

    def _drain_resource_queue(self, cfg: dict) -> dict:
        state = load_box_state()
        now_ts = int(time.time())
        self._sync_resource_queue_from_decisions(state, cfg, now_ts)
        queue = dict(state.get("resource_wait_queue") or {})
        if not queue:
            save_box_state(state)
            return {"checked": 0, "added": [], "remaining": 0, "skipped_resource": 0, "deferred": 0}

        qb = QBittorrentClient(cfg)
        qitems = qb.torrents(tagged_only=True)
        traffic = traffic_snapshot(cfg, state=state, persist=True)
        state = load_box_state()
        queue = dict(state.get("resource_wait_queue") or queue)
        disk = disk_snapshot(cfg)
        observations = dict(state.get("torrent_observations") or {})
        retry_at = dict(state.get("watch_retry_at") or {})
        seen = set(str(x) for x in (state.get("seen_ids") or []))
        recheck = max(20, int(cfg.get("resource_queue_recheck_seconds") or 60))
        max_checks = max(1, min(20, int(cfg.get("resource_queue_checks_per_run") or 3)))
        api = box_api_budget_snapshot(cfg, now_ts=now_ts)
        detail_remaining = int((api.get("detail") or {}).get("remaining") or 0)
        download_remaining = int((api.get("download") or {}).get("remaining") or 0)
        checked = 0
        added = []
        skipped_resource = 0
        deferred = 0

        for tid, queued in sorted(queue.items(), key=_queue_sort_key, reverse=True):
            if checked >= max_checks:
                break
            if tid in seen:
                queue.pop(tid, None)
                retry_at.pop(tid, None)
                continue
            if int(queued.get("next_retry_at") or 0) > now_ts:
                deferred += 1
                continue

            # 先用队列里已有的 size 做廉价资源判断。仍然明显塞不下时不要浪费 detail 配额。
            rough_ok, rough_why = self._rough_resource_fit(queued, cfg, qitems, traffic, disk)
            if not rough_ok:
                skipped_resource += 1
                queued["last_wait_at"] = now_ts
                queued["next_retry_at"] = now_ts + recheck
                queued["reason"] = rough_why
                queue[tid] = queued
                retry_at[tid] = now_ts + recheck
                continue

            if detail_remaining <= 0:
                break

            checked += 1
            detail_remaining -= 1
            try:
                detail = box_torrent_detail(tid, cfg)
                meta = mteam_client._torrent_meta(detail)
                history = add_observation(
                    observations.get(tid) or [],
                    int(meta.get("seeders") or 0),
                    int(meta.get("leechers") or 0),
                    now_ts,
                )
                observations[tid] = history
                verdict = evaluate_torrent(meta, cfg, history=history)
                row = _decision_row(tid, queued.get("name") or tid, meta, verdict)

                if verdict.get("permanent"):
                    row["result"] = "reject"
                    row["reason"] += " · 资源等待期间已失去抢流价值"
                    self._decision(state, row)
                    queue.pop(tid, None)
                    retry_at.pop(tid, None)
                    observations.pop(tid, None)
                    seen.add(tid)
                    continue

                if not verdict.get("accepted"):
                    delay = watch_retry_delay(verdict.get("age_seconds") or 0, verdict.get("trend"))
                    row["result"] = "resource-recheck-watch"
                    row["reason"] += " · 空间已可用，但重新评估后暂不值得下载"
                    self._decision(state, row)
                    queued.update({
                        "name": meta.get("name") or queued.get("name") or tid,
                        "last_wait_at": now_ts,
                        "next_retry_at": now_ts + delay,
                        "score": float(verdict.get("score") or 0),
                        "priority": float(verdict.get("priority") or 0),
                        "size_gb": float(meta.get("size_gb") or 0),
                        "reason": row.get("reason") or "",
                    })
                    queue[tid] = queued
                    retry_at[tid] = now_ts + delay
                    continue

                # detail 是实时数据，再用实时体积做正式资源判断。
                qitems = qb.torrents(tagged_only=True)
                disk = disk_snapshot(cfg)
                allowed, why = self._resource_gate(meta, cfg, qitems, traffic, disk)
                if not allowed:
                    row["result"] = "wait-resource"
                    row["reason"] += " · 资源队列复查：" + why
                    self._decision(state, row)
                    queued.update({
                        "name": meta.get("name") or queued.get("name") or tid,
                        "last_wait_at": now_ts,
                        "next_retry_at": now_ts + recheck,
                        "score": float(verdict.get("score") or 0),
                        "priority": float(verdict.get("priority") or 0),
                        "size_gb": float(meta.get("size_gb") or 0),
                        "reason": why,
                    })
                    queue[tid] = queued
                    retry_at[tid] = now_ts + recheck
                    continue

                if download_remaining <= 0:
                    queued["last_wait_at"] = now_ts
                    queued["next_retry_at"] = now_ts + 120
                    queued["reason"] = "种子下载 API 配额等待"
                    queue[tid] = queued
                    retry_at[tid] = now_ts + 120
                    break

                download_remaining -= 1
                content = box_torrent_bytes(tid, cfg)
                qb.add_torrent(content, _safe_filename(tid, meta.get("name") or tid))
                row["result"] = "added-resource-retry"
                row["reason"] += " · 之前因资源不足等待；空间/下载槽释放后重新评估仍值得，现已自动补抓"
                self._decision(state, row)
                append_download_log(
                    f"盒子资源释放后补抓 id={tid} score={verdict.get('score')} "
                    f"priority={verdict.get('priority')} size={meta.get('size_gb')}GB "
                    f"S/L={meta.get('seeders')}/{meta.get('leechers')}",
                    action="box_add_resource_retry",
                    torrent_id=tid,
                    name=meta.get("name") or "",
                    score=verdict.get("score"),
                    priority=verdict.get("priority"),
                    size_gb=meta.get("size_gb"),
                    seeders=meta.get("seeders"),
                    leechers=meta.get("leechers"),
                )
                queue.pop(tid, None)
                retry_at.pop(tid, None)
                observations.pop(tid, None)
                seen.add(tid)
                added.append(tid)

                # 默认同时下载=1；即使配置更高，也重新读一次 qBit 后再决定下一只。
                qitems = qb.torrents(tagged_only=True)
                disk = disk_snapshot(cfg)
                if int(QBittorrentClient.summary(qitems).get("active_downloads") or 0) >= max(
                    1, int(cfg.get("max_active_downloads") or 1)
                ):
                    break
            except BoxApiBudgetError:
                break
            except Exception as e:
                queued["last_wait_at"] = now_ts
                queued["next_retry_at"] = now_ts + 180
                queued["reason"] = str(e)[:300]
                queue[tid] = queued
                retry_at[tid] = now_ts + 180
                self._decision(state, {
                    "torrent_id": tid,
                    "name": queued.get("name") or tid,
                    "result": "resource-retry-error",
                    "reason": str(e)[:300],
                })

        state["resource_wait_queue"] = queue
        state["watch_retry_at"] = retry_at
        state["torrent_observations"] = observations
        state["seen_ids"] = list(seen)
        save_box_state(state)
        return {
            "checked": checked,
            "added": added,
            "remaining": len(queue),
            "skipped_resource": skipped_resource,
            "deferred": deferred,
        }

    def run_once(self) -> dict:
        cfg = load_box_config()

        # 旧版把 cleanup 放在扫描末尾；V4 先腾空间，让本轮就能利用刚释放的容量。
        pre_cleanup = self.cleanup() if cfg.get("auto_cleanup") else {"deleted": []}

        state = load_box_state()
        now_ts = int(time.time())
        migrated = self._sync_resource_queue_from_decisions(state, cfg, now_ts)
        save_box_state(state)

        queue_result = self._drain_resource_queue(cfg)

        # 再处理当前 RSS；若本轮新产生 wait-resource，会在下面同步进持久队列。
        result = super().run_once()

        state = load_box_state()
        queued_now = self._sync_resource_queue_from_decisions(state, cfg, int(time.time()))
        save_box_state(state)

        if isinstance(result, dict):
            result["pre_cleanup"] = pre_cleanup
            result["resource_queue"] = {
                **queue_result,
                "migrated": migrated,
                "queued_now": queued_now,
                "remaining": len(state.get("resource_wait_queue") or {}),
            }
        return result

    def status(self) -> dict:
        out = super().status()
        try:
            state = load_box_state()
            queue = state.get("resource_wait_queue") or {}
            out["resource_queue"] = {
                "count": len(queue) if isinstance(queue, dict) else 0,
                "items": sorted(
                    [
                        {"torrent_id": tid, **row}
                        for tid, row in (queue.items() if isinstance(queue, dict) else [])
                        if isinstance(row, dict)
                    ],
                    key=lambda row: (
                        float(row.get("priority") or 0),
                        float(row.get("score") or 0),
                    ),
                    reverse=True,
                )[:20],
            }
        except Exception as e:
            out["resource_queue"] = {"count": 0, "error": str(e)}
        return out


controller = BoxControllerV4()
