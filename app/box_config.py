import json
from threading import Lock

from app.store import DATA_DIR, ensure_dirs

BOX_CONFIG_FILE = DATA_DIR / "box_config.json"
BOX_STATE_FILE = DATA_DIR / "box_state.json"
_lock = Lock()

DEFAULT_BOX_CONFIG = {
    "enabled": False,
    "rss_url": "",
    "rss_poll_seconds": 90,
    "qbit_url": "http://127.0.0.1:8080",
    "qbit_username": "admin",
    "qbit_password": "",
    "qbit_tag": "mteam-box",
    "qbit_category": "mteam-box",
    "download_dir": "/srv/torrents/downloads",
    "vnstat_interface": "eth0",
    "min_size_gb": 0.3,
    "max_size_gb": 9.0,
    "max_age_seconds": 900,
    "min_leechers": 4,
    "max_seeders": 25,
    "min_demand": 0.5,
    "min_score": 65.0,
    "max_active_downloads": 1,
    "data_cap_gb": 16.0,
    "disk_reserve_gb": 4.0,
    "traffic_budget_gb": 1400.0,
    "traffic_hard_stop_gb": 1500.0,
    "billing_reset_day": 1,
    "auto_cleanup": True,
    "cleanup_ratio": 2.85,
    "cleanup_idle_minutes": 360,
    "cleanup_min_seed_minutes": 1440,
    "max_rss_items_per_run": 30,
}

DEFAULT_BOX_STATE = {
    "seen_ids": [],
    "rss_warmed_up": False,
    "rss_source_fp": "",
    "watch_retry_at": {},
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
    out["decisions"] = list(out.get("decisions") or [])[-100:]
    retry = out.get("watch_retry_at") or {}
    if isinstance(retry, dict):
        out["watch_retry_at"] = dict(list(retry.items())[-500:])
    else:
        out["watch_retry_at"] = {}
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
