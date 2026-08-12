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
from app.box_service_v4 import BoxControllerV4, _queue_sort_key
from app.mteam import client as mteam_client
from app.qbittorrent import QBittorrentClient
from app.store import append_download_log


GIB = 1024 ** 3
_RESOURCE_REASON_MARKERS = (
    "盒子数据上限",
    "磁盘安全预留",
    "下载槽",
    "已有下载任务",
    "资源不足",
)


def logical_data_bytes(items: list) -> int:
    """盒子当前真正完成的数据量。

    旧版使用 torrent.total_size 作为已占数据，因此 0% 的 8GB torrent 也会提前占掉 8GB
    逻辑配额。这里按 progress * size 计算已完成内容；完整任务仍按完整 size 计算。
    物理磁盘是否安全由 disk_free - candidate_size >= reserve 单独兜底。
    """
    total = 0
    for item in items or []:
        try:
            size = max(0, int(item.get("size") or 0))
            progress = max(0.0, min(1.0, float(item.get("progress") or 0)))
        except Exception:
            continue
        if progress >= 0.9999:
            total += size
        else:
            total += int(size * progress)
    return total


def was_resource_blocked(row: dict) -> bool:
    reason = str((row or {}).get("reason") or "")
    return any(marker in reason for marker in _RESOURCE_REASON_MARKERS)


class BoxControllerV5(BoxControllerV4):
    """V4 + 资源释放即时唤醒 + 实际数据占用模型 + 等待候选宽限期。"""

    @staticmethod
    def _rough_resource_fit(row: dict, cfg: dict, qitems: list, traffic: dict, disk: dict) -> tuple:
        if traffic.get("budget_reached"):
            return False, "月流量软预算已到"

        summary = QBittorrentClient.summary(qitems)
        max_active = max(1, int(cfg.get("max_active_downloads") or 1))
        if int(summary.get("active_downloads") or 0) >= max_active:
            return False, "下载槽仍被占用"

        size_gb = max(0.0, float((row or {}).get("size_gb") or 0))
        used_gb = logical_data_bytes(qitems) / GIB
        data_cap = float(cfg.get("data_cap_gb") or 0)
        if data_cap > 0 and size_gb > 0 and used_gb + size_gb > data_cap:
            return False, (
                f"盒子数据上限仍不足：实际已占 {used_gb:.2f}GB + 候选 {size_gb:.2f}GB > {data_cap:.1f}GB"
            )

        reserve = float(cfg.get("disk_reserve_gb") or 0)
        free_gb = float(disk.get("free_gb") or 0)
        if size_gb > 0 and free_gb - size_gb < reserve:
            return False, (
                f"磁盘安全预留仍不足：可用 {free_gb:.2f}GB - 候选 {size_gb:.2f}GB < 预留 {reserve:.1f}GB"
            )
        return True, ""

    def _resource_gate(self, meta: dict, cfg: dict, qitems: list, traffic: dict, disk: dict) -> tuple:
        """主 RSS 和资源队列共用的容量判断。

        数据上限按当前实际完成内容计算；磁盘门槛仍按“候选完整体积”预留，避免把盘写爆。
        """
        summary = QBittorrentClient.summary(qitems)
        size_gb = float(meta.get("size_gb") or 0)

        if traffic.get("budget_reached"):
            return False, f"月流量软预算已到 {traffic.get('used_gb')}GB"
        if int(summary.get("active_downloads") or 0) >= int(cfg.get("max_active_downloads") or 1):
            return False, "已有下载任务，等待当前任务完成"

        used_gb = logical_data_bytes(qitems) / GIB
        data_cap = float(cfg.get("data_cap_gb") or 0)
        if data_cap > 0 and used_gb + size_gb > data_cap:
            return False, (
                f"盒子数据上限 {data_cap:.1f}GB（实际已占 {used_gb:.2f}GB，候选 {size_gb:.2f}GB）"
            )

        reserve = float(cfg.get("disk_reserve_gb") or 0)
        free_gb = float(disk.get("free_gb") or 0)
        if free_gb - size_gb < reserve:
            return False, (
                f"磁盘安全预留 {reserve:.1f}GB（当前可用 {free_gb:.2f}GB，候选 {size_gb:.2f}GB）"
            )
        return True, ""

    def _drain_resource_queue(self, cfg: dict) -> dict:
        state = load_box_state()
        now_ts = int(time.time())
        self._sync_resource_queue_from_decisions(state, cfg, now_ts)
        # 关键：先把同步后的队列写盘，避免后续重新读取状态时拿回旧 next_retry_at。
        save_box_state(state)

        queue = dict(state.get("resource_wait_queue") or {})
        if not queue:
            return {
                "checked": 0,
                "added": [],
                "remaining": 0,
                "skipped_resource": 0,
                "deferred": 0,
                "woken": 0,
            }

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
        grace_age = max(
            int(cfg.get("hard_max_age_seconds") or 3600),
            int(cfg.get("resource_queue_hard_max_age_seconds") or 7200),
        )

        api = box_api_budget_snapshot(cfg, now_ts=now_ts)
        detail_remaining = int((api.get("detail") or {}).get("remaining") or 0)
        download_remaining = int((api.get("download") or {}).get("remaining") or 0)

        checked = 0
        added = []
        skipped_resource = 0
        deferred = 0
        woken = 0

        for tid, queued in sorted(queue.items(), key=_queue_sort_key, reverse=True):
            if checked >= max_checks:
                break
            if tid in seen:
                queue.pop(tid, None)
                retry_at.pop(tid, None)
                continue

            # 先看资源是否真的还卡着。若现在已经放得下，而且此前就是资源原因排队，
            # 直接无视旧 next_retry_at 唤醒；这是 V4 对“已存在旧队列”遗漏的关键修复。
            qitems = qb.torrents(tagged_only=True)
            disk = disk_snapshot(cfg)
            rough_ok, rough_why = self._rough_resource_fit(queued, cfg, qitems, traffic, disk)
            next_retry = int(queued.get("next_retry_at") or 0)

            if not rough_ok:
                skipped_resource += 1
                if next_retry <= now_ts:
                    queued["last_wait_at"] = now_ts
                    queued["next_retry_at"] = now_ts + recheck
                    queued["reason"] = rough_why
                    queue[tid] = queued
                    retry_at[tid] = now_ts + recheck
                continue

            if next_retry > now_ts and not was_resource_blocked(queued):
                deferred += 1
                continue
            if next_retry > now_ts and was_resource_blocked(queued):
                woken += 1

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

                # 它当初已经通过评分，只是没有空间。给资源等待候选更长绝对观察窗口，
                # 但仍用实时 S/L、趋势和更严格的晚期门槛重新评估，绝不盲目补抓。
                eval_cfg = dict(cfg)
                eval_cfg["hard_max_age_seconds"] = grace_age
                verdict = evaluate_torrent(meta, eval_cfg, history=history)
                row = _decision_row(tid, queued.get("name") or tid, meta, verdict)

                if verdict.get("permanent"):
                    row["result"] = "reject"
                    row["reason"] += " · 资源等待宽限期内也已失去抢流价值"
                    self._decision(state, row)
                    queue.pop(tid, None)
                    retry_at.pop(tid, None)
                    observations.pop(tid, None)
                    seen.add(tid)
                    continue

                if not verdict.get("accepted"):
                    delay = watch_retry_delay(verdict.get("age_seconds") or 0, verdict.get("trend"))
                    row["result"] = "resource-recheck-watch"
                    row["reason"] += " · 资源已经释放，但实时需求重新评估后暂不值得下载"
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

                qitems = qb.torrents(tagged_only=True)
                disk = disk_snapshot(cfg)
                allowed, why = self._resource_gate(meta, cfg, qitems, traffic, disk)
                if not allowed:
                    row["result"] = "wait-resource"
                    row["reason"] += " · 资源队列实时复查：" + why
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
                row["reason"] += " · 之前因资源不足排队；容量释放后实时评估仍值得，已自动补抓"
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

        final_items = qb.torrents(tagged_only=True)
        final_disk = disk_snapshot(cfg)
        return {
            "checked": checked,
            "added": added,
            "remaining": len(queue),
            "skipped_resource": skipped_resource,
            "deferred": deferred,
            "woken": woken,
            "logical_data_gb": round(logical_data_bytes(final_items) / GIB, 2),
            "disk_free_gb": final_disk.get("free_gb"),
        }


controller = BoxControllerV5()
