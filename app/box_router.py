from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import auth_user
from app.box_config import (
    load_box_config,
    load_box_state,
    masked_box_config,
    save_box_config,
    save_box_state,
)
from app.box_service import controller, traffic_snapshot
from app.qbittorrent import QBittorrentClient
from app.store import append_access_log

router = APIRouter()


class BoxConfigIn(BaseModel):
    enabled: Optional[bool] = None
    rss_url: Optional[str] = None
    clear_rss_url: Optional[bool] = False
    rss_poll_seconds: Optional[int] = None
    qbit_url: Optional[str] = None
    qbit_username: Optional[str] = None
    qbit_password: Optional[str] = None
    clear_qbit_password: Optional[bool] = False
    qbit_tag: Optional[str] = None
    qbit_category: Optional[str] = None
    download_dir: Optional[str] = None
    vnstat_interface: Optional[str] = None

    min_size_gb: Optional[float] = None
    max_size_gb: Optional[float] = None
    max_age_seconds: Optional[int] = None
    hard_max_age_seconds: Optional[int] = None
    min_leechers: Optional[int] = None
    max_seeders: Optional[int] = None
    min_demand: Optional[float] = None
    min_score: Optional[float] = None

    race_lane_enabled: Optional[bool] = None
    race_min_size_gb: Optional[float] = None
    race_tier1_max_size_gb: Optional[float] = None
    race_tier1_max_age_seconds: Optional[int] = None
    race_tier2_max_size_gb: Optional[float] = None
    race_tier2_max_age_seconds: Optional[int] = None
    race_tier3_max_size_gb: Optional[float] = None
    race_tier3_max_age_seconds: Optional[int] = None
    race_candidates_per_run: Optional[int] = None

    max_active_downloads: Optional[int] = None
    data_cap_gb: Optional[float] = None
    disk_reserve_gb: Optional[float] = None
    traffic_budget_gb: Optional[float] = None
    traffic_hard_stop_gb: Optional[float] = None
    billing_reset_day: Optional[int] = None

    detail_limit_per_hour: Optional[int] = None
    download_limit_per_hour: Optional[int] = None

    auto_cleanup: Optional[bool] = None
    cleanup_ratio: Optional[float] = None
    cleanup_idle_minutes: Optional[int] = None
    cleanup_min_seed_minutes: Optional[int] = None
    cleanup_stalled_zero_minutes: Optional[int] = None
    cleanup_stalled_partial_minutes: Optional[int] = None

    resource_queue_recheck_seconds: Optional[int] = None
    resource_queue_checks_per_run: Optional[int] = None
    resource_queue_max_items: Optional[int] = None
    resource_queue_hard_max_age_seconds: Optional[int] = None

    experiment_enabled: Optional[bool] = None
    max_rss_items_per_run: Optional[int] = None


class BoxTorrentActionIn(BaseModel):
    hashes: List[str]
    action: str


@router.get("/box")
def box_page():
    return FileResponse("static/box.html")


@router.get("/api/box/config")
def box_get_config(user: str = Depends(auth_user)):
    return masked_box_config(load_box_config())


def _clamp_int(data: dict, key: str, low: int, high: int):
    if key in data:
        data[key] = max(low, min(high, int(data[key])))


def _clamp_float(data: dict, key: str, low: float, high: float):
    if key in data:
        data[key] = max(low, min(high, float(data[key])))


@router.post("/api/box/config")
def box_update_config(body: BoxConfigIn, user: str = Depends(auth_user)):
    cfg = load_box_config()
    data = body.model_dump(exclude_none=True)
    clear_rss = bool(data.pop("clear_rss_url", False))
    clear_pwd = bool(data.pop("clear_qbit_password", False))

    rss = (data.get("rss_url") or "").strip() if "rss_url" in data else None
    if clear_rss:
        data["rss_url"] = ""
    elif rss == "":
        data.pop("rss_url", None)
    elif rss is not None:
        data["rss_url"] = rss

    pwd = data.get("qbit_password") if "qbit_password" in data else None
    if clear_pwd:
        data["qbit_password"] = ""
    elif pwd == "":
        data.pop("qbit_password", None)

    _clamp_int(data, "rss_poll_seconds", 20, 3600)
    _clamp_int(data, "billing_reset_day", 1, 31)
    _clamp_int(data, "max_active_downloads", 1, 20)
    _clamp_int(data, "max_rss_items_per_run", 1, 100)
    _clamp_int(data, "max_age_seconds", 60, 86400)
    _clamp_int(data, "hard_max_age_seconds", 300, 172800)
    _clamp_int(data, "detail_limit_per_hour", 1, 100)
    _clamp_int(data, "download_limit_per_hour", 1, 100)
    _clamp_int(data, "race_candidates_per_run", 1, 20)
    _clamp_int(data, "race_tier1_max_age_seconds", 20, 3600)
    _clamp_int(data, "race_tier2_max_age_seconds", 20, 3600)
    _clamp_int(data, "race_tier3_max_age_seconds", 20, 3600)
    _clamp_int(data, "cleanup_idle_minutes", 1, 10080)
    _clamp_int(data, "cleanup_min_seed_minutes", 0, 43200)
    _clamp_int(data, "cleanup_stalled_zero_minutes", 1, 1440)
    _clamp_int(data, "cleanup_stalled_partial_minutes", 1, 2880)
    _clamp_int(data, "resource_queue_recheck_seconds", 20, 3600)
    _clamp_int(data, "resource_queue_checks_per_run", 1, 20)
    _clamp_int(data, "resource_queue_max_items", 10, 500)
    _clamp_int(data, "resource_queue_hard_max_age_seconds", 300, 172800)

    for key in (
        "min_size_gb", "max_size_gb", "race_min_size_gb",
        "race_tier1_max_size_gb", "race_tier2_max_size_gb", "race_tier3_max_size_gb",
        "data_cap_gb", "disk_reserve_gb", "traffic_budget_gb", "traffic_hard_stop_gb",
    ):
        _clamp_float(data, key, 0.0, 100000.0)
    _clamp_float(data, "min_demand", 0.0, 1000.0)
    _clamp_float(data, "min_score", -1000.0, 1000.0)
    _clamp_float(data, "cleanup_ratio", 0.0, 1000.0)

    # Race 三档按体积递增，避免前端误填出相互覆盖的区间。
    t1 = float(data.get("race_tier1_max_size_gb", cfg.get("race_tier1_max_size_gb") or 2.0))
    t2 = float(data.get("race_tier2_max_size_gb", cfg.get("race_tier2_max_size_gb") or 4.0))
    t3 = float(data.get("race_tier3_max_size_gb", cfg.get("race_tier3_max_size_gb") or 6.0))
    if not (t1 <= t2 <= t3):
        raise HTTPException(400, "Race 分档体积必须满足：第一档 ≤ 第二档 ≤ 第三档")

    soft = int(data.get("max_age_seconds", cfg.get("max_age_seconds") or 900))
    if "hard_max_age_seconds" in data:
        data["hard_max_age_seconds"] = max(soft, int(data["hard_max_age_seconds"]))
    qhard = int(data.get("resource_queue_hard_max_age_seconds", cfg.get("resource_queue_hard_max_age_seconds") or 7200))
    data["resource_queue_hard_max_age_seconds"] = max(
        int(data.get("hard_max_age_seconds", cfg.get("hard_max_age_seconds") or 3600)), qhard
    )

    cfg.update(data)
    cfg = save_box_config(cfg)
    if cfg.get("enabled"):
        controller.start()
    else:
        controller.stop()
    append_access_log("盒子模式配置已更新", action="box_config", user=user, enabled=bool(cfg.get("enabled")))
    return {"ok": True, "config": masked_box_config(cfg)}


@router.get("/api/box/status")
def box_status(user: str = Depends(auth_user)):
    return controller.status()


@router.post("/api/box/start")
def box_start(user: str = Depends(auth_user)):
    cfg = load_box_config()
    cfg["enabled"] = True
    save_box_config(cfg)
    controller.start()
    append_access_log("盒子模式已启动", action="box_start", user=user)
    return controller.status()


@router.post("/api/box/stop")
def box_stop(user: str = Depends(auth_user)):
    cfg = load_box_config()
    cfg["enabled"] = False
    save_box_config(cfg)
    controller.stop()
    append_access_log("盒子模式已停止", action="box_stop", user=user)
    return controller.status()


@router.post("/api/box/run")
def box_run_once(user: str = Depends(auth_user)):
    try:
        return controller.run_once()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/api/box/cleanup")
def box_cleanup(user: str = Depends(auth_user)):
    try:
        return controller.cleanup()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/api/box/qbit/test")
def box_qbit_test(user: str = Depends(auth_user)):
    try:
        return QBittorrentClient(load_box_config()).test()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/api/box/torrents")
def box_torrents(user: str = Depends(auth_user)):
    try:
        qb = QBittorrentClient(load_box_config())
        items = qb.torrents(tagged_only=True)
        return {"items": items, "summary": qb.summary(items)}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/api/box/torrents/action")
def box_torrent_action(body: BoxTorrentActionIn, user: str = Depends(auth_user)):
    try:
        qb = QBittorrentClient(load_box_config())
        action = (body.action or "").strip().lower()
        if action == "pause":
            return qb.pause(body.hashes)
        if action == "resume":
            return qb.resume(body.hashes)
        if action == "delete":
            return qb.delete(body.hashes, delete_files=False)
        if action in ("delete-data", "delete_data"):
            return qb.delete(body.hashes, delete_files=True)
        raise HTTPException(400, "未知操作")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/api/box/traffic/reset")
def box_traffic_reset(user: str = Depends(auth_user)):
    state = load_box_state()
    state["traffic_cycle_key"] = ""
    state["traffic_baseline_bytes"] = None
    save_box_state(state)
    try:
        snap = traffic_snapshot(load_box_config(), state=load_box_state(), persist=True)
    except Exception as e:
        raise HTTPException(400, str(e))
    append_access_log("盒子流量基线已重置", action="box_traffic_reset", user=user)
    return {"ok": True, "traffic": snap}
