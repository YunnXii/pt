import time

from app.box_cleanup import plan_stalled_download_cleanup
from app.box_config import load_box_config, load_box_state, save_box_state
from app.box_service_v2 import BoxControllerV2
from app.qbittorrent import QBittorrentClient
from app.store import append_download_log


class BoxControllerV3(BoxControllerV2):
    """V2 抢流决策 + 未完成下载卡死清理。"""

    def cleanup(self) -> dict:
        # 先执行原有的已完成任务清理：ratio 到顶 / 长期空闲。
        base = super().cleanup()

        cfg = load_box_config()
        qb = QBittorrentClient(cfg)
        items = qb.torrents(tagged_only=True)
        state = load_box_state()
        now_ts = int(time.time())

        plan = plan_stalled_download_cleanup(
            items,
            cfg,
            progress_state=state.get("download_progress_state") or {},
            now_ts=now_ts,
        )

        stalled_delete = list(plan.get("delete") or [])
        reasons = dict(plan.get("reasons") or {})
        if stalled_delete:
            qb.delete(stalled_delete, delete_files=True)
            by_hash = {str(x.get("hash") or ""): x for x in items}
            for h in stalled_delete:
                torrent = by_hash.get(h) or {}
                reason = reasons.get(h) or "下载卡死"
                append_download_log(
                    f"盒子自动淘汰卡死下载 {torrent.get('name') or h}：{reason}",
                    action="box_cleanup_stalled",
                    torrent_hash=h,
                    torrent_name=torrent.get("name") or "",
                    reason=reason,
                    state=torrent.get("state") or "",
                    downloaded=torrent.get("downloaded") or 0,
                    progress=torrent.get("progress") or 0,
                )
                self._decision(state, {
                    "result": "cleanup-stalled",
                    "name": torrent.get("name") or h,
                    "reason": reason,
                })

        state["download_progress_state"] = plan.get("progress_state") or {}
        save_box_state(state)

        base_deleted = list(base.get("deleted") or []) if isinstance(base, dict) else []
        merged_reasons = dict(base.get("reasons") or {}) if isinstance(base, dict) else {}
        merged_reasons.update(reasons)
        return {
            "ok": True,
            "deleted": base_deleted + stalled_delete,
            "reasons": merged_reasons,
            "stalled_deleted": stalled_delete,
            "stalled_zero_minutes": plan.get("zero_minutes"),
            "stalled_partial_minutes": plan.get("partial_minutes"),
        }


controller = BoxControllerV3()
