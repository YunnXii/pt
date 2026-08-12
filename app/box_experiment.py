import re
import time


MILESTONES = (
    (60, "m1"),
    (180, "m3"),
    (300, "m5"),
    (600, "m10"),
)


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _latest_add_strategy(decisions: list) -> dict:
    """name-normalized -> strategy/torrent_id，用最近一次 added 类决策做回填。"""
    out = {}
    for row in reversed(decisions or []):
        if not isinstance(row, dict):
            continue
        result = str(row.get("result") or "")
        if result not in ("added", "added-resource-retry", "added-race"):
            continue
        key = _norm_name(row.get("name") or "")
        if not key or key in out:
            continue
        out[key] = {
            "strategy": "race" if result == "added-race" else "trend",
            "torrent_id": str(row.get("torrent_id") or ""),
            "entry_age_seconds": int(row.get("age_seconds") or 0),
            "entry_seeders": int(row.get("seeders") or 0),
            "entry_leechers": int(row.get("leechers") or 0),
            "entry_score": float(row.get("score") or 0),
        }
    return out


def register_experiment(
    state: dict,
    *,
    torrent_id: str,
    name: str,
    strategy: str,
    qbit_hash: str = "",
    added_at: int = 0,
    size_gb: float = 0,
    entry_age_seconds: int = 0,
    entry_seeders: int = 0,
    entry_leechers: int = 0,
    entry_score: float = 0,
) -> dict:
    experiments = dict(state.get("experiments") or {})
    key = str(qbit_hash or torrent_id or _norm_name(name))
    old = experiments.get(key) if isinstance(experiments.get(key), dict) else {}
    row = {
        **old,
        "torrent_id": str(torrent_id or old.get("torrent_id") or ""),
        "qbit_hash": str(qbit_hash or old.get("qbit_hash") or ""),
        "name": str(name or old.get("name") or "")[:220],
        "strategy": str(strategy or old.get("strategy") or "unknown"),
        "added_at": int(added_at or old.get("added_at") or time.time()),
        "size_gb": float(size_gb or old.get("size_gb") or 0),
        "entry_age_seconds": int(entry_age_seconds or old.get("entry_age_seconds") or 0),
        "entry_seeders": int(entry_seeders if entry_seeders is not None else old.get("entry_seeders") or 0),
        "entry_leechers": int(entry_leechers if entry_leechers is not None else old.get("entry_leechers") or 0),
        "entry_score": float(entry_score or old.get("entry_score") or 0),
        "milestones": dict(old.get("milestones") or {}),
        "latest": dict(old.get("latest") or {}),
    }
    experiments[key] = row
    state["experiments"] = experiments
    return row


def _find_experiment_key(experiments: dict, item: dict):
    h = str(item.get("hash") or "")
    if h and h in experiments:
        return h
    name_key = _norm_name(item.get("name") or "")
    if not name_key:
        return None
    for key, row in experiments.items():
        if not isinstance(row, dict):
            continue
        if _norm_name(row.get("name") or "") == name_key:
            return key
    return None


def sync_experiments(state: dict, qitems: list, now_ts: int = None) -> dict:
    """回填 trend/race 任务，并持续记录 1/3/5/10 分钟表现。"""
    now_ts = int(now_ts or time.time())
    experiments = dict(state.get("experiments") or {})
    decision_map = _latest_add_strategy(state.get("decisions") or [])

    # 当前仍在 qBit 的任务：若还没实验记录，尽量从最近 added 决策回填。
    for item in qitems or []:
        key = _find_experiment_key(experiments, item)
        if key is None:
            d = decision_map.get(_norm_name(item.get("name") or ""))
            if not d:
                continue
            h = str(item.get("hash") or "")
            key = h or str(d.get("torrent_id") or _norm_name(item.get("name") or ""))
            experiments[key] = {
                "torrent_id": str(d.get("torrent_id") or ""),
                "qbit_hash": h,
                "name": str(item.get("name") or "")[:220],
                "strategy": d.get("strategy") or "trend",
                "added_at": int(item.get("added_on") or now_ts),
                "size_gb": float(item.get("size") or 0) / (1024 ** 3),
                "entry_age_seconds": int(d.get("entry_age_seconds") or 0),
                "entry_seeders": int(d.get("entry_seeders") or 0),
                "entry_leechers": int(d.get("entry_leechers") or 0),
                "entry_score": float(d.get("entry_score") or 0),
                "milestones": {},
                "latest": {},
            }

    # 建一个名字索引，处理极少数 hash 未及时识别的情况。
    by_name = {_norm_name(x.get("name") or ""): x for x in (qitems or []) if _norm_name(x.get("name") or "")}

    for key, row in list(experiments.items()):
        if not isinstance(row, dict):
            continue
        item = None
        h = str(row.get("qbit_hash") or "")
        if h:
            item = next((x for x in (qitems or []) if str(x.get("hash") or "") == h), None)
        if item is None:
            item = by_name.get(_norm_name(row.get("name") or ""))
        if item is None:
            continue

        if not row.get("qbit_hash") and item.get("hash"):
            row["qbit_hash"] = str(item.get("hash"))
        if not row.get("added_at") and item.get("added_on"):
            row["added_at"] = int(item.get("added_on"))

        added_at = int(row.get("added_at") or now_ts)
        age = max(0, now_ts - added_at)
        latest = {
            "observed_at": now_ts,
            "age_seconds": age,
            "downloaded": int(item.get("downloaded") or 0),
            "uploaded": int(item.get("uploaded") or 0),
            "ratio": round(float(item.get("ratio") or 0), 3),
            "progress": round(float(item.get("progress") or 0), 4),
            "upspeed": int(item.get("upspeed") or 0),
            "dlspeed": int(item.get("dlspeed") or 0),
            "state": str(item.get("state") or ""),
        }
        row["latest"] = latest
        milestones = dict(row.get("milestones") or {})
        for seconds, label in MILESTONES:
            if age >= seconds and label not in milestones:
                milestones[label] = dict(latest)
        row["milestones"] = milestones
        experiments[key] = row

    state["experiments"] = experiments
    return experiments


def experiment_summary(experiments: dict) -> dict:
    groups = {}
    for strategy in ("race", "trend"):
        rows = [
            x for x in (experiments or {}).values()
            if isinstance(x, dict) and x.get("strategy") == strategy and isinstance(x.get("latest"), dict) and x.get("latest")
        ]
        ratios = [float((x.get("latest") or {}).get("ratio") or 0) for x in rows]
        uploads = [int((x.get("latest") or {}).get("uploaded") or 0) for x in rows]
        downloads = [int((x.get("latest") or {}).get("downloaded") or 0) for x in rows]
        completed = [x for x in rows if float((x.get("latest") or {}).get("progress") or 0) >= 0.9999]
        groups[strategy] = {
            "count": len(rows),
            "completed": len(completed),
            "avg_ratio": round(sum(ratios) / len(ratios), 2) if ratios else 0.0,
            "ratio_ge_1": len([r for r in ratios if r >= 1.0]),
            "ratio_ge_2": len([r for r in ratios if r >= 2.0]),
            "ratio_ge_2_rate": round(100 * len([r for r in ratios if r >= 2.0]) / len(ratios), 1) if ratios else 0.0,
            "uploaded_bytes": sum(uploads),
            "downloaded_bytes": sum(downloads),
        }
    return groups
