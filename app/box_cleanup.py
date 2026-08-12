def _i(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def plan_stalled_download_cleanup(items: list, cfg: dict, progress_state: dict = None, now_ts: int = None) -> dict:
    """根据 qBittorrent 下载字节是否继续增长，判断未完成任务是否已经卡死。

    只自动处理 stalledDL；pausedDL / 正常 downloading / 已完成任务都不会被这套规则删除。
    progress_state 由盒子状态持久化，用于记录“最后一次 downloaded 字节增长”的时间。
    """
    import time

    now_ts = int(now_ts if now_ts is not None else time.time())
    zero_minutes = max(0, _i(cfg.get("cleanup_stalled_zero_minutes"), 15))
    partial_minutes = max(0, _i(cfg.get("cleanup_stalled_partial_minutes"), 30))
    old_state = progress_state if isinstance(progress_state, dict) else {}
    next_state = {}
    delete = []
    reasons = {}

    for torrent in items or []:
        h = str(torrent.get("hash") or "")
        if not h:
            continue

        progress = float(torrent.get("progress") or 0)
        if progress >= 0.9999:
            continue

        downloaded = max(0, _i(torrent.get("downloaded"), 0))
        dlspeed = max(0, _i(torrent.get("dlspeed"), 0))
        state_name = str(torrent.get("state") or "").strip().lower()
        added_on = max(0, _i(torrent.get("added_on"), 0))

        previous = old_state.get(h) if isinstance(old_state.get(h), dict) else {}
        first_seen = _i(previous.get("first_seen_at"), now_ts)
        prev_downloaded = _i(previous.get("downloaded"), -1)
        last_progress = _i(previous.get("last_progress_at"), 0)

        # 第一次观察、字节数回退（重校验/重加）或确实有新增下载，都重新起算“无进展时间”。
        if prev_downloaded < 0 or downloaded != prev_downloaded:
            last_progress = now_ts
        if last_progress <= 0:
            last_progress = now_ts

        row = {
            "downloaded": downloaded,
            "first_seen_at": first_seen,
            "last_progress_at": last_progress,
            "state": state_name,
        }
        next_state[h] = row

        # 只杀 qBittorrent 明确认定为 stalledDL 的未完成任务。
        if state_name != "stalleddl" or dlspeed > 0:
            continue

        if downloaded <= 0:
            # 对 0B 僵尸直接使用 qBit 的 added_on；这样升级代码后，已经卡了很久的旧任务可立刻清掉。
            start = added_on if added_on > 0 else first_seen
            stalled_seconds = max(0, now_ts - start)
            if zero_minutes > 0 and stalled_seconds >= zero_minutes * 60:
                delete.append(h)
                reasons[h] = f"stalledDL 且始终 0B，已等待 {stalled_seconds // 60} 分钟（阈值 {zero_minutes} 分钟）"
            continue

        stalled_seconds = max(0, now_ts - last_progress)
        if partial_minutes > 0 and stalled_seconds >= partial_minutes * 60:
            delete.append(h)
            reasons[h] = (
                f"stalledDL 且下载字节连续 {stalled_seconds // 60} 分钟未增长"
                f"（阈值 {partial_minutes} 分钟）"
            )

    for h in delete:
        next_state.pop(h, None)

    return {
        "delete": delete,
        "reasons": reasons,
        "progress_state": next_state,
        "zero_minutes": zero_minutes,
        "partial_minutes": partial_minutes,
    }
