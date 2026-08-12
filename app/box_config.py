import json
from threading import Lock

from app.store import DATA_DIR, ensure_dirs

BOX_CONFIG_FILE = DATA_DIR / "box_config.json"
BOX_STATE_FILE = DATA_DIR / "box_state.json"
_lock = Lock()

DEFAULT_BOX_CONFIG = {
    "enabled": False,
    "rss_url": "",
    "rss_poll_seconds": 45,
    "qbit_url": "http://127.0.0.1:8080",
    "qbit_username": "admin",
    "qbit_password": "",
    "qbit_tag": "mteam-box",
    "qbit_category": "mteam-box",
    "download_dir": "/srv/torrents/downloads",
    "vnstat_interface": "eth0",
    "min_size_gb": 0.3,
    "max_size_gb": 9.0,
    # 软黄金窗口：超过后仍可被高需求/强趋势救回来。
    "max_age_seconds": 900,
    # 绝对观察上限：超过才永久放弃。
    "hard_max_age_seconds": 3600,
    "min_leechers": 4,
    # Seeder 仅作为竞争参考上限，不再直接永久拒绝。
    "max_seeders": 25,
    "min_demand": 0.5,
    "min_score": 65.0,
    "max_active_downloads": 1,
    "data_cap_gb": 16.0,
    "disk_reserve_gb": 4.0,
    "traffic_budget_gb": 1400.0,
    "traffic_hard_stop_gb": 1500.0,
    "billing_reset_day": 1,
    # 盒子不再共用原项目 max_actions_per_hour=40；按 M-Team 接口单独留安全余量。
    "detail_limit_per_hour": 90,
    "download_limit_per_hour": 80,
    "auto_cleanup": True,
    "cleanup_ratio": 2.85,
    "cleanup_idle_minutes": 360,
    "cleanup_min_seed_minutes": 1440,
    # 下载卡死清理：0B 僵尸更快清理；已有部分数据则给更长恢复时间。
    "cleanup_stalled_zero_minutes": 15,
    "cleanup_stalled_partial_minutes": 30,
    # 资源等待队列：即使种子滚出 RSS，也会在空间/下载槽释放后重新评估。
    "resource_queue_recheck_seconds": 60,
    "resource_queue_checks_per_run": 3,
    "resource_queue_max_items": 120,
    "max_rss_items_per_run": 30,
}

DEFAULT_BOX_STATE = {
    "seen_ids": [],
    "rss_warmed_up": False,
    "rss_source_fp": "",
    "watch_retry_at": {},
    "torrent_observations": {},
    # torrent id -> 等待原因/上次分数/下次复查时间；独立于 RSS 当前窗口持久化。
    "resource_wait_queue": {},
    # hash -> downloaded / first_seen_at / last_progress_at，用于判断未完成下载是否真正停止推进。
    "download_progress_state": {},
    "traffic_cycle_key": "",
    "traffic_baseline_bytes": None,
    "last_run_at": "",
    "last_error": "",
    "last_rss_title": "",
    "decisions": [],
}


def _merge(defaults: dict, value: dict) -> dict:
    out = dict(defaults)
    if isinstance(value, dict):
        out.update(value)
    return out


def load_box_config() -> dict:
    ensure_dirs()
    if not BOX_CONFIG_FILE.exists():
        return dict(DEFAULT_BOX_CONFIG)
    try:
        return _merge(DEFAULT_BOX_CONFIG, json.loads(BOX_CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        return dict(DEFAULT_BOX_CONFIG)


def save_box_config(cfg: dict) -> dict:
    ensure_dirs()
    out = _merge(DEFAULT_BOX_CONFIG, cfg)
    with _lock:
        BOX_CONFIG_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_box_state() -> dict:
    ensure_dirs()
    if not BOX_STATE_FILE.exists():
        return dict(DEFAULT_BOX_STATE)
    try:
        return _merge(DEFAULT_BOX_STATE, json.loads(BOX_STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return dict(DEFAULT_BOX_STATE)


def save_box_state(state: dict) -> dict:
    ensure_dirs()
    out = _merge(DEFAULT_BOX_STATE, state)
    out["seen_ids"] = [str(x) for x in (out.get("seen_ids") or [])][-2000:]
    out["decisions"] = list(out.get("decisions") or [])[-120:]

    retry = out.get("watch_retry_at") or {}
    if isinstance(retry, dict):
        out["watch_retry_at"] = dict(list(retry.items())[-500:])
    else:
        out["watch_retry_at"] = {}

    observations = out.get("torrent_observations") or {}
    if isinstance(observations, dict):
        compact = {}
        for tid, rows in list(observations.items())[-500:]:
            if isinstance(rows, list):
                compact[str(tid)] = [x for x in rows if isinstance(x, dict)][-12:]
        out["torrent_observations"] = compact
    else:
        out["torrent_observations"] = {}

    queue = out.get("resource_wait_queue") or {}
    if isinstance(queue, dict):
        compact_queue = {}
        for tid, row in list(queue.items())[-200:]:
            if not isinstance(row, dict):
                continue
            compact_queue[str(tid)] = {
                "name": str(row.get("name") or "")[:180],
                "first_wait_at": int(row.get("first_wait_at") or 0),
                "last_wait_at": int(row.get("last_wait_at") or 0),
                "next_retry_at": int(row.get("next_retry_at") or 0),
                "score": float(row.get("score") or 0),
                "priority": float(row.get("priority") or 0),
                "size_gb": float(row.get("size_gb") or 0),
                "reason": str(row.get("reason") or "")[:300],
            }
        out["resource_wait_queue"] = compact_queue
    else:
        out["resource_wait_queue"] = {}

    progress_state = out.get("download_progress_state") or {}
    if isinstance(progress_state, dict):
        compact_progress = {}
        for h, row in list(progress_state.items())[-200:]:
            if isinstance(row, dict):
                compact_progress[str(h)] = {
                    "downloaded": int(row.get("downloaded") or 0),
                    "first_seen_at": int(row.get("first_seen_at") or 0),
                    "last_progress_at": int(row.get("last_progress_at") or 0),
                    "state": str(row.get("state") or ""),
                }
        out["download_progress_state"] = compact_progress
    else:
        out["download_progress_state"] = {}

    with _lock:
        BOX_STATE_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def masked_box_config(cfg: dict = None) -> dict:
    cfg = dict(cfg or load_box_config())
    rss = (cfg.get("rss_url") or "").strip()
    pwd = cfg.get("qbit_password") or ""
    cfg["rss_url_set"] = bool(rss)
    cfg["qbit_password_set"] = bool(pwd)
    cfg["rss_url"] = ""
    cfg["qbit_password"] = ""
    return cfg
