import json
import time
from pathlib import Path
from threading import Lock

from app.mteam import client as mteam_client
from app.store import DATA_DIR, append_pt_log, ensure_dirs


BOX_API_BUDGET_FILE = DATA_DIR / "box_api_budget.json"
_budget_lock = Lock()


class BoxApiBudgetError(RuntimeError):
    def __init__(self, kind: str, used: int, limit: int):
        self.kind = kind
        self.used = int(used)
        self.limit = int(limit)
        label = "torrent/detail" if kind == "detail" else "种子下载"
        super().__init__(f"盒子 {label} 独立配额已达 {used}/{limit}，等待滚动一小时窗口释放")


def rolling_window_usage(timestamps: list, limit: int, now_ts: int = None) -> dict:
    now_ts = int(now_ts if now_ts is not None else time.time())
    cutoff = now_ts - 3600
    rows = []
    for value in timestamps or []:
        try:
            ts = int(value)
        except Exception:
            continue
        if ts > cutoff and ts <= now_ts + 60:
            rows.append(ts)
    rows.sort()
    limit = max(1, int(limit or 1))
    used = len(rows)
    remaining = max(0, limit - used)
    reset_in = 0
    if rows and used >= limit:
        reset_in = max(1, rows[0] + 3600 - now_ts)
    return {
        "timestamps": rows,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "reset_in_seconds": reset_in,
    }


def _load_budget_file() -> dict:
    ensure_dirs()
    if not BOX_API_BUDGET_FILE.exists():
        return {"detail": [], "download": []}
    try:
        data = json.loads(BOX_API_BUDGET_FILE.read_text(encoding="utf-8")) or {}
        return {
            "detail": list(data.get("detail") or []),
            "download": list(data.get("download") or []),
        }
    except Exception:
        return {"detail": [], "download": []}


def _save_budget_file(data: dict):
    ensure_dirs()
    payload = {
        "detail": [int(x) for x in (data.get("detail") or [])][-200:],
        "download": [int(x) for x in (data.get("download") or [])][-200:],
    }
    BOX_API_BUDGET_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def reserve_api_call(kind: str, limit: int, now_ts: int = None) -> dict:
    if kind not in ("detail", "download"):
        raise ValueError(f"未知盒子 API 配额类型: {kind}")
    now_ts = int(now_ts if now_ts is not None else time.time())
    with _budget_lock:
        data = _load_budget_file()
        usage = rolling_window_usage(data.get(kind) or [], limit, now_ts)
        if usage["remaining"] <= 0:
            # 顺手把过期时间戳清掉，保持文件紧凑。
            data[kind] = usage["timestamps"]
            _save_budget_file(data)
            raise BoxApiBudgetError(kind, usage["used"], usage["limit"])
        rows = list(usage["timestamps"])
        rows.append(now_ts)
        data[kind] = rows
        _save_budget_file(data)
        return rolling_window_usage(rows, limit, now_ts)


def box_api_budget_snapshot(cfg: dict, now_ts: int = None) -> dict:
    now_ts = int(now_ts if now_ts is not None else time.time())
    detail_limit = max(1, int(cfg.get("detail_limit_per_hour") or 90))
    download_limit = max(1, int(cfg.get("download_limit_per_hour") or 80))
    with _budget_lock:
        data = _load_budget_file()
        detail = rolling_window_usage(data.get("detail") or [], detail_limit, now_ts)
        download = rolling_window_usage(data.get("download") or [], download_limit, now_ts)
        # 读取状态时也清理过期项，但不新增调用。
        data["detail"] = detail["timestamps"]
        data["download"] = download["timestamps"]
        _save_budget_file(data)
    return {"detail": detail, "download": download}


def box_torrent_detail(torrent_id: str, cfg: dict) -> dict:
    """盒子专用 detail：绕开原项目 40/h 拟人限额，使用盒子自己的滚动预算。"""
    limit = max(1, int(cfg.get("detail_limit_per_hour") or 90))
    reserve_api_call("detail", limit)
    with mteam_client._client() as c:
        r = c.post(
            mteam_client._api("/api/torrent/detail"),
            headers=mteam_client._headers(False),
            data={"id": str(torrent_id)},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (0, "0"):
            raise RuntimeError(data.get("message") or str(data))
        return data.get("data") or {}


def box_torrent_bytes(torrent_id: str, cfg: dict) -> bytes:
    """盒子专用种子下载：genDlToken + torrent GET 共用一次下载行为预算。"""
    limit = max(1, int(cfg.get("download_limit_per_hour") or 80))
    reserve_api_call("download", limit)
    with mteam_client._client() as c:
        r = c.post(
            mteam_client._api("/api/torrent/genDlToken"),
            headers=mteam_client._headers(False),
            data={"id": str(torrent_id)},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (0, "0"):
            raise RuntimeError(data.get("message") or str(data))
        url = data.get("data")
        if not url:
            raise RuntimeError("未获取到下载链接")
        r = c.get(url, headers=mteam_client._headers(False))
        r.raise_for_status()
        if not r.content or len(r.content) < 50:
            raise RuntimeError("M-Team 返回的种子内容为空")
        content = bytes(r.content)
    append_pt_log(
        f"盒子获取种子成功 id={torrent_id}",
        action="box_gen_dl_token",
        torrent_id=str(torrent_id),
        bytes=len(content),
    )
    return content
