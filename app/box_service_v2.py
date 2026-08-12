import hashlib
import time

from app.box_config import load_box_config, load_box_state, save_box_state
from app.box_service import (
    BoxController,
    _safe_filename,
    _torrent_bytes,
    disk_snapshot,
    fetch_rss,
    score_torrent,
    traffic_snapshot,
)
from app.mteam import client as mteam_client
from app.qbittorrent import QBittorrentClient
from app.store import append_download_log, append_pt_log, load_config
from app.timeutil import now_str


JUNK_TITLE_MARKERS = (
    "错误种子请删除",
    "錯誤種子請刪除",
    "错误种子，请删除",
    "錯誤種子，請刪除",
    "error torrent please delete",
    "invalid torrent please delete",
)


def rss_source_fingerprint(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:16]


def is_junk_rss_title(title: str) -> bool:
    text = " ".join((title or "").strip().lower().split())
    if not text:
        return False
    if any(marker.lower() in text for marker in JUNK_TITLE_MARKERS):
        return True
    if text in {"deleted", "invalid", "error torrent", "torrent deleted"}:
        return True
    return False


def watch_retry_delay(age_seconds: int) -> int:
    age = max(0, int(age_seconds or 0))
    if age < 120:
        return 120
    if age < 300:
        return 180
    return 300


class BoxControllerV2(BoxController):
    """在原盒子控制器上增加 RSS 暖机、垃圾种预过滤与观察退避。"""

    def run_once(self) -> dict:
        cfg = load_box_config()
        if not (load_config().get("api_key") or mteam_client.token):
            raise RuntimeError("主站点尚未配置 M-Team API Key")
        rss_url = (cfg.get("rss_url") or "").strip()
        if not rss_url:
            raise RuntimeError("盒子模式尚未配置 RSS 地址")

        state = load_box_state()
        feed = fetch_rss(rss_url)
        state["last_rss_title"] = feed.get("title") or ""
        source_fp = rss_source_fingerprint(rss_url)
        feed_items = feed.get("items") or []

        # 首次启用 / RSS 地址变化：只登记当前列表为基线，不逐条查询详情。
        # 这样不会把几十条历史种当成刚发现的新种，也不会白白消耗 detail 请求。
        if not state.get("rss_warmed_up") or state.get("rss_source_fp") != source_fp:
            seen = set(str(x) for x in (state.get("seen_ids") or []))
            baseline_ids = [str(x.get("id") or "") for x in feed_items if str(x.get("id") or "")]
            seen.update(baseline_ids)
            state["seen_ids"] = list(seen)
            state["rss_warmed_up"] = True
            state["rss_source_fp"] = source_fp
            state["watch_retry_at"] = {}
            state["last_run_at"] = now_str()
            state["last_error"] = ""
            self._decision(state, {
                "result": "warmup",
                "name": feed.get("title") or "RSS",
                "reason": f"首次暖机：已登记当前 {len(baseline_ids)} 条 RSS 为基线，从下一条新种开始判断",
            })
            save_box_state(state)
            append_pt_log(
                f"盒子 RSS 暖机 feed={feed.get('title') or '-'} baseline={len(baseline_ids)}",
                action="box_warmup",
                baseline=len(baseline_ids),
            )
            return {
                "ok": True,
                "warmup": True,
                "feed": feed.get("title") or "",
                "processed": 0,
                "added": [],
                "pending": [],
                "rejected": [],
                "message": f"RSS 暖机完成，登记 {len(baseline_ids)} 条现有项目；后续只处理新出现的种子",
            }

        qb = QBittorrentClient(cfg)
        qitems = qb.torrents(tagged_only=True)
        traffic = traffic_snapshot(cfg, state=state, persist=True)
        state = load_box_state()
        disk = disk_snapshot(cfg)

        if traffic.get("hard_stop_reached"):
            hashes = [x.get("hash") for x in qitems if x.get("hash")]
            qb.pause(hashes)
            msg = f"流量硬停止线已到 {traffic.get('used_gb')}GB，已暂停盒子任务"
            self._decision(state, {"result": "hard-stop", "reason": msg})
            state["last_run_at"] = now_str()
            state["last_error"] = ""
            save_box_state(state)
            return {"ok": True, "hard_stop": True, "message": msg}

        seen = set(str(x) for x in (state.get("seen_ids") or []))
        retry_at = dict(state.get("watch_retry_at") or {})
        max_items = max(1, min(100, int(cfg.get("max_rss_items_per_run") or 30)))
        processed = 0
        added = []
        pending = []
        rejected = []
        skipped_backoff = 0
        now_ts = int(time.time())

        for item in feed_items[:max_items]:
            tid = str(item.get("id") or "")
            if not tid or tid in seen:
                continue

            title = item.get("title") or ""
            if is_junk_rss_title(title):
                seen.add(tid)
                retry_at.pop(tid, None)
                rejected.append(tid)
                self._decision(state, {
                    "torrent_id": tid,
                    "name": title or tid,
                    "result": "reject",
                    "score": 0,
                    "reason": "RSS 已标记为错误/删除种，未查询详情 API",
                })
                continue

            next_retry = int(retry_at.get(tid) or 0)
            if next_retry > now_ts:
                skipped_backoff += 1
                continue

            processed += 1
            try:
                detail = mteam_client.torrent_detail(tid)
                meta = mteam_client._torrent_meta(detail)
                verdict = score_torrent(meta, cfg)
                row = {
                    "torrent_id": tid,
                    "name": meta.get("name") or title or tid,
                    "size_gb": meta.get("size_gb"),
                    "seeders": meta.get("seeders"),
                    "leechers": meta.get("leechers"),
                    "age_seconds": verdict.get("age_seconds"),
                    "demand": verdict.get("demand"),
                    "score": verdict.get("score"),
                    "reason": " · ".join(verdict.get("reasons") or []),
                }
                if not verdict.get("accepted"):
                    row["result"] = "reject" if verdict.get("permanent") else "watch"
                    self._decision(state, row)
                    if verdict.get("permanent"):
                        seen.add(tid)
                        retry_at.pop(tid, None)
                        rejected.append(tid)
                    else:
                        delay = watch_retry_delay(verdict.get("age_seconds") or 0)
                        retry_at[tid] = now_ts + delay
                        pending.append(tid)
                    continue

                qitems = qb.torrents(tagged_only=True)
                traffic = traffic_snapshot(cfg, state=state, persist=True)
                state = load_box_state()
                disk = disk_snapshot(cfg)
                allowed, why = self._resource_gate(meta, cfg, qitems, traffic, disk)
                if not allowed:
                    row["result"] = "wait-resource"
                    row["reason"] = (row.get("reason") or "") + " · " + why
                    self._decision(state, row)
                    retry_at[tid] = now_ts + 180
                    pending.append(tid)
                    if traffic.get("budget_reached"):
                        break
                    continue

                content = _torrent_bytes(tid)
                qb.add_torrent(content, _safe_filename(tid, meta.get("name") or tid))
                seen.add(tid)
                retry_at.pop(tid, None)
                row["result"] = "added"
                self._decision(state, row)
                added.append(tid)
                append_download_log(
                    f"盒子抢流已推送 qBittorrent id={tid} score={verdict.get('score')} "
                    f"size={meta.get('size_gb')}GB S/L={meta.get('seeders')}/{meta.get('leechers')}",
                    action="box_add",
                    torrent_id=tid,
                    name=meta.get("name") or "",
                    score=verdict.get("score"),
                    size_gb=meta.get("size_gb"),
                    seeders=meta.get("seeders"),
                    leechers=meta.get("leechers"),
                )
            except Exception as e:
                self._decision(state, {
                    "torrent_id": tid,
                    "name": title or tid,
                    "result": "error",
                    "reason": str(e)[:300],
                })
                retry_at[tid] = now_ts + 300
                pending.append(tid)

        state["seen_ids"] = list(seen)
        state["watch_retry_at"] = retry_at
        state["rss_warmed_up"] = True
        state["rss_source_fp"] = source_fp
        state["last_run_at"] = now_str()
        state["last_error"] = ""
        save_box_state(state)

        cleanup = self.cleanup() if cfg.get("auto_cleanup") else {"deleted": []}
        append_pt_log(
            f"盒子 RSS 扫描 feed={feed.get('title') or '-'} processed={processed} added={len(added)} "
            f"watch={len(pending)} reject={len(rejected)} backoff={skipped_backoff} "
            f"cleanup={len(cleanup.get('deleted') or [])}",
            action="box_scan",
            processed=processed,
            added=added,
            pending=pending[:20],
            rejected=rejected[:20],
            skipped_backoff=skipped_backoff,
        )
        return {
            "ok": True,
            "feed": feed.get("title") or "",
            "processed": processed,
            "added": added,
            "pending": pending,
            "rejected": rejected,
            "skipped_backoff": skipped_backoff,
            "cleanup": cleanup,
            "traffic": traffic,
            "disk": disk,
        }


controller = BoxControllerV2()
