import calendar
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.box_config import load_box_config, load_box_state, save_box_state
from app.mteam import client as mteam_client
from app.qbittorrent import QBittorrentClient
from app.store import append_download_log, append_pt_log, load_config
from app.timeutil import APP_TZ, now, now_str

GIB = 1024 ** 3


def _gb(n) -> float:
    try:
        return float(n or 0) / GIB
    except Exception:
        return 0.0


def _parse_created(value: str):
    text = (value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.replace(tzinfo=APP_TZ) if dt.tzinfo is None else dt.astimezone(APP_TZ)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            chunk = text[:19] if len(text) > 10 else text[:10]
            return datetime.strptime(chunk, fmt if len(chunk) > 10 else "%Y-%m-%d").replace(tzinfo=APP_TZ)
        except Exception:
            continue
    return None


def score_torrent(meta: dict, cfg: dict, now_dt=None) -> dict:
    """盒子抢流评分。返回 accepted/permanent，临时低需求会继续观察到 max_age。"""
    now_dt = now_dt or now()
    size_gb = float(meta.get("size_gb") or 0)
    seeders = max(0, int(meta.get("seeders") or 0))
    leechers = max(0, int(meta.get("leechers") or 0))
    created = _parse_created(meta.get("created_date") or "")
    age = max(0, int((now_dt - created).total_seconds())) if created else 999999
    min_size = float(cfg.get("min_size_gb") or 0)
    max_size = float(cfg.get("max_size_gb") or 0)
    max_age = max(30, int(cfg.get("max_age_seconds") or 600))
    min_leechers = max(0, int(cfg.get("min_leechers") or 0))
    max_seeders = max(0, int(cfg.get("max_seeders") or 0))
    min_demand = max(0.0, float(cfg.get("min_demand") or 0))
    min_score = float(cfg.get("min_score") or 0)
    reasons = []

    if size_gb <= 0:
        return {"accepted": False, "permanent": True, "score": 0, "age_seconds": age, "demand": 0, "reasons": ["无有效体积"]}
    if min_size > 0 and size_gb < min_size:
        return {"accepted": False, "permanent": True, "score": 0, "age_seconds": age, "demand": 0, "reasons": [f"体积过小 {size_gb:.2f}GB"]}
    if max_size > 0 and size_gb > max_size:
        return {"accepted": False, "permanent": True, "score": 0, "age_seconds": age, "demand": 0, "reasons": [f"体积过大 {size_gb:.2f}GB"]}
    if age > max_age:
        return {"accepted": False, "permanent": True, "score": 0, "age_seconds": age, "demand": 0, "reasons": [f"已错过抢流窗口 {age}s"]}
    if max_seeders > 0 and seeders > max_seeders:
        return {"accepted": False, "permanent": True, "score": 0, "age_seconds": age, "demand": leechers / (seeders + 1), "reasons": [f"做种已过多 {seeders}"]}

    score = 0.0
    if age <= 90:
        score += 52
        reasons.append(f"极新 {age}s")
    elif age <= 180:
        score += 42
        reasons.append(f"很新 {age}s")
    elif age <= 300:
        score += 30
        reasons.append(f"新种 {age}s")
    else:
        score += 14
        reasons.append(f"尚在窗口 {age}s")

    demand = leechers / (seeders + 1)
    score += min(38.0, demand * 12.0)
    score += min(24.0, leechers * 1.2)
    score -= min(25.0, max(0, seeders - 3) * 0.9)

    if 1.0 <= size_gb <= 4.0:
        score += 8
        reasons.append(f"甜点体积 {size_gb:.2f}GB")
    elif size_gb <= 6.0:
        score += 3
        reasons.append(f"小体积 {size_gb:.2f}GB")

    reasons.append(f"S/L={seeders}/{leechers}")
    reasons.append(f"需求比={demand:.2f}")

    temporary = False
    if leechers < min_leechers:
        temporary = True
        reasons.append(f"下载者不足 {leechers}<{min_leechers}")
    if demand < min_demand:
        temporary = True
        reasons.append(f"需求比不足 {demand:.2f}<{min_demand:.2f}")
    if score < min_score:
        temporary = True
        reasons.append(f"评分不足 {score:.1f}<{min_score:.1f}")

    return {
        "accepted": not temporary,
        "permanent": False,
        "score": round(score, 1),
        "age_seconds": age,
        "demand": round(demand, 3),
        "reasons": reasons,
    }


def _localname(tag: str) -> str:
    return str(tag or "").split("}")[-1].lower()


def _node_text(node, names) -> str:
    names = {x.lower() for x in names}
    for child in list(node):
        if _localname(child.tag) in names:
            text = (child.text or "").strip()
            if text:
                return text
            href = (child.attrib.get("href") or child.attrib.get("url") or "").strip()
            if href:
                return href
    return ""


def _extract_torrent_id(*values) -> str:
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.isdigit():
            return text
        try:
            q = parse_qs(urlparse(text).query)
            for key in ("id", "tid", "torrent_id", "torrentId"):
                vals = q.get(key)
                if vals and str(vals[0]).isdigit():
                    return str(vals[0])
        except Exception:
            pass
        for pat in (
            r"/(?:detail|torrent|torrents)/(\d+)(?:\D|$)",
            r"[?&](?:id|tid|torrent_id|torrentId)=(\d+)",
            r"\b(?:torrent|tid|id)[:=_-](\d+)\b",
        ):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
    return ""


def fetch_rss(url: str) -> dict:
    import httpx

    if not (url or "").strip():
        raise RuntimeError("请先配置 M-Team RSS 地址")
    r = httpx.get(
        url.strip(),
        headers={"User-Agent": "MTeamBox/0.1 (+RSS)"},
        timeout=30.0,
        follow_redirects=True,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    feed_title = ""
    for node in root.iter():
        if _localname(node.tag) == "title" and (node.text or "").strip():
            feed_title = (node.text or "").strip()
            break
    entries = [x for x in root.iter() if _localname(x.tag) in ("item", "entry")]
    items = []
    for node in entries:
        title = _node_text(node, {"title"})
        link = _node_text(node, {"link"})
        guid = _node_text(node, {"guid", "id"})
        published = _node_text(node, {"pubdate", "published", "updated", "date"})
        enclosure = ""
        for child in list(node):
            if _localname(child.tag) == "enclosure":
                enclosure = (child.attrib.get("url") or child.attrib.get("href") or "").strip()
                break
        tid = _extract_torrent_id(guid, link, enclosure)
        items.append({
            "id": tid,
            "title": title,
            "link": link,
            "guid": guid,
            "enclosure": enclosure,
            "published": published,
        })
    return {"title": feed_title, "items": items}


def _cycle_start_key(reset_day: int) -> str:
    n = now()
    reset_day = max(1, min(31, int(reset_day or 1)))

    def at(y, m):
        d = min(reset_day, calendar.monthrange(y, m)[1])
        return datetime(y, m, d, tzinfo=APP_TZ)

    start = at(n.year, n.month)
    if n < start:
        prev = (n.replace(day=1) - timedelta(days=1))
        start = at(prev.year, prev.month)
    return start.strftime("%Y-%m-%d")


def vnstat_total_bytes(interface: str = "eth0") -> int:
    p = subprocess.run(
        ["vnstat", "--json", "-i", interface],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    import json

    data = json.loads(p.stdout or "{}")
    interfaces = data.get("interfaces") or []
    if not interfaces:
        raise RuntimeError("vnStat 尚无网卡数据")
    traffic = (interfaces[0].get("traffic") or {})
    total = traffic.get("total") or {}
    return int(total.get("rx") or 0) + int(total.get("tx") or 0)


def traffic_snapshot(cfg: dict, state: dict = None, persist: bool = True) -> dict:
    state = dict(state or load_box_state())
    iface = (cfg.get("vnstat_interface") or "eth0").strip() or "eth0"
    total = vnstat_total_bytes(iface)
    key = _cycle_start_key(int(cfg.get("billing_reset_day") or 1))
    baseline = state.get("traffic_baseline_bytes")
    if state.get("traffic_cycle_key") != key or baseline is None or int(baseline) > total:
        baseline = total
        state["traffic_cycle_key"] = key
        state["traffic_baseline_bytes"] = total
        if persist:
            save_box_state(state)
    used = max(0, total - int(baseline or 0))
    budget = float(cfg.get("traffic_budget_gb") or 0)
    hard = float(cfg.get("traffic_hard_stop_gb") or 0)
    return {
        "interface": iface,
        "cycle_key": key,
        "used_bytes": used,
        "used_gb": round(_gb(used), 2),
        "budget_gb": budget,
        "hard_stop_gb": hard,
        "budget_reached": bool(budget > 0 and _gb(used) >= budget),
        "hard_stop_reached": bool(hard > 0 and _gb(used) >= hard),
    }


def disk_snapshot(cfg: dict) -> dict:
    p = Path((cfg.get("download_dir") or "/").strip() or "/")
    target = p if p.exists() else Path("/")
    total, used, free = shutil.disk_usage(target)
    return {
        "path": str(target),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "free_gb": round(_gb(free), 2),
        "reserve_gb": float(cfg.get("disk_reserve_gb") or 0),
    }


def _torrent_bytes(torrent_id: str) -> bytes:
    url = mteam_client.gen_dl_token(str(torrent_id), human=False)
    with mteam_client._client() as c:
        r = c.get(url, headers=mteam_client._headers(False))
        r.raise_for_status()
        if not r.content or len(r.content) < 50:
            raise RuntimeError("M-Team 返回的种子内容为空")
        return bytes(r.content)


def _safe_filename(tid: str, name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name or tid)[:120]
    return f"{tid}_{safe}.torrent"


class BoxController:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    def start_if_enabled(self):
        if load_box_config().get("enabled"):
            self.start()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._running = True
                return
            self._stop.clear()
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="mteam-box", daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                self._record_error(str(e))
            cfg = load_box_config()
            sec = max(20, int(cfg.get("rss_poll_seconds") or 60))
            if self._stop.wait(sec):
                break

    def _record_error(self, message: str):
        state = load_box_state()
        state["last_error"] = str(message)[:500]
        state["last_run_at"] = now_str()
        save_box_state(state)
        append_pt_log(f"盒子模式异常: {message}", action="box", level="error", error=str(message))

    def _decision(self, state: dict, row: dict):
        row = dict(row)
        row["at"] = now_str()
        state.setdefault("decisions", []).append(row)
        state["decisions"] = state["decisions"][-100:]

    def _resource_gate(self, meta: dict, cfg: dict, qitems: list, traffic: dict, disk: dict) -> tuple:
        summary = QBittorrentClient.summary(qitems)
        size_gb = float(meta.get("size_gb") or 0)
        if traffic.get("budget_reached"):
            return False, f"月流量软预算已到 {traffic.get('used_gb')}GB"
        if int(summary.get("active_downloads") or 0) >= int(cfg.get("max_active_downloads") or 1):
            return False, "已有下载任务，等待当前任务完成"
        data_cap = float(cfg.get("data_cap_gb") or 0)
        if data_cap > 0 and _gb(summary.get("total_size") or 0) + size_gb > data_cap:
            return False, f"盒子数据上限 {data_cap:.1f}GB"
        reserve = float(cfg.get("disk_reserve_gb") or 0)
        if float(disk.get("free_gb") or 0) - size_gb < reserve:
            return False, f"磁盘需保留 {reserve:.1f}GB"
        return True, ""

    def run_once(self) -> dict:
        cfg = load_box_config()
        if not (load_config().get("api_key") or mteam_client.token):
            raise RuntimeError("主站点尚未配置 M-Team API Key")
        rss_url = (cfg.get("rss_url") or "").strip()
        if not rss_url:
            raise RuntimeError("盒子模式尚未配置 RSS 地址")

        state = load_box_state()
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

        feed = fetch_rss(rss_url)
        state["last_rss_title"] = feed.get("title") or ""
        seen = set(str(x) for x in (state.get("seen_ids") or []))
        max_items = max(1, min(100, int(cfg.get("max_rss_items_per_run") or 30)))
        processed = 0
        added = []
        pending = []
        rejected = []

        for item in (feed.get("items") or [])[:max_items]:
            tid = str(item.get("id") or "")
            if not tid or tid in seen:
                continue
            processed += 1
            try:
                detail = mteam_client.torrent_detail(tid)
                meta = mteam_client._torrent_meta(detail)
                verdict = score_torrent(meta, cfg)
                row = {
                    "torrent_id": tid,
                    "name": meta.get("name") or item.get("title") or tid,
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
                        rejected.append(tid)
                    else:
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
                    pending.append(tid)
                    if traffic.get("budget_reached"):
                        break
                    continue

                content = _torrent_bytes(tid)
                qb.add_torrent(content, _safe_filename(tid, meta.get("name") or tid))
                seen.add(tid)
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
                    "name": item.get("title") or tid,
                    "result": "error",
                    "reason": str(e)[:300],
                })
                pending.append(tid)

        state["seen_ids"] = list(seen)
        state["last_run_at"] = now_str()
        state["last_error"] = ""
        save_box_state(state)

        cleanup = self.cleanup() if cfg.get("auto_cleanup") else {"deleted": []}
        append_pt_log(
            f"盒子 RSS 扫描 feed={feed.get('title') or '-'} processed={processed} added={len(added)} "
            f"watch={len(pending)} reject={len(rejected)} cleanup={len(cleanup.get('deleted') or [])}",
            action="box_scan",
            processed=processed,
            added=added,
            pending=pending[:20],
            rejected=rejected[:20],
        )
        return {
            "ok": True,
            "feed": feed.get("title") or "",
            "processed": processed,
            "added": added,
            "pending": pending,
            "rejected": rejected,
            "cleanup": cleanup,
            "traffic": traffic,
            "disk": disk,
        }

    def cleanup(self) -> dict:
        cfg = load_box_config()
        qb = QBittorrentClient(cfg)
        items = qb.torrents(tagged_only=True)
        ratio_limit = float(cfg.get("cleanup_ratio") or 0)
        idle_minutes = max(0, int(cfg.get("cleanup_idle_minutes") or 0))
        min_seed_minutes = max(0, int(cfg.get("cleanup_min_seed_minutes") or 0))
        now_ts = int(time.time())
        delete = []
        reasons = {}
        for t in items:
            if float(t.get("progress") or 0) < 0.9999:
                continue
            h = t.get("hash") or ""
            ratio = float(t.get("ratio") or 0)
            if ratio_limit > 0 and ratio >= ratio_limit:
                delete.append(h)
                reasons[h] = f"分享率 {ratio:.2f} 达到 {ratio_limit:.2f}"
                continue
            completion = int(t.get("completion_on") or 0)
            last = int(t.get("last_activity") or completion or 0)
            seed_age = now_ts - completion if completion > 0 else 0
            idle = now_ts - last if last > 0 else 0
            if (
                idle_minutes > 0
                and completion > 0
                and seed_age >= min_seed_minutes * 60
                and idle >= idle_minutes * 60
                and int(t.get("upspeed") or 0) == 0
            ):
                delete.append(h)
                reasons[h] = f"完成后空闲 {idle // 60} 分钟"

        if delete:
            qb.delete(delete, delete_files=True)
            for t in items:
                h = t.get("hash") or ""
                if h in reasons:
                    append_download_log(
                        f"盒子自动淘汰 {t.get('name')}：{reasons[h]}",
                        action="box_cleanup",
                        torrent_hash=h,
                        torrent_name=t.get("name") or "",
                        reason=reasons[h],
                        ratio=t.get("ratio"),
                    )
        return {"ok": True, "deleted": delete, "reasons": reasons}

    def status(self) -> dict:
        cfg = load_box_config()
        state = load_box_state()
        out = {
            "running": bool(self._running and self._thread and self._thread.is_alive()),
            "enabled": bool(cfg.get("enabled")),
            "last_run_at": state.get("last_run_at") or "",
            "last_error": state.get("last_error") or "",
            "last_rss_title": state.get("last_rss_title") or "",
            "decisions": list(state.get("decisions") or [])[-30:][::-1],
        }
        try:
            out["traffic"] = traffic_snapshot(cfg, state=state, persist=True)
        except Exception as e:
            out["traffic"] = {"error": str(e)}
        try:
            out["disk"] = disk_snapshot(cfg)
        except Exception as e:
            out["disk"] = {"error": str(e)}
        try:
            qb = QBittorrentClient(cfg)
            items = qb.torrents(tagged_only=True)
            out["qbit"] = {"summary": qb.summary(items), "items": items}
        except Exception as e:
            out["qbit"] = {"error": str(e), "summary": {}, "items": []}
        return out


controller = BoxController()
