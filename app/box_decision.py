from datetime import datetime

from app.box_service import _parse_created
from app.timeutil import now


def _f(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _i(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def add_observation(history: list, seeders: int, leechers: int, ts: int) -> list:
    rows = [dict(x) for x in (history or []) if isinstance(x, dict)]
    row = {"ts": int(ts), "seeders": max(0, int(seeders)), "leechers": max(0, int(leechers))}
    if rows and int(rows[-1].get("ts") or 0) == row["ts"]:
        rows[-1] = row
    else:
        rows.append(row)
    cutoff = int(ts) - 3600
    rows = [x for x in rows if int(x.get("ts") or 0) >= cutoff]
    return rows[-12:]


def trend_stats(history: list) -> dict:
    rows = [x for x in (history or []) if isinstance(x, dict) and int(x.get("ts") or 0) > 0]
    if len(rows) < 2:
        return {
            "samples": len(rows),
            "elapsed_seconds": 0,
            "delta_seeders": 0,
            "delta_leechers": 0,
            "leecher_per_minute": 0.0,
            "rising": False,
            "summary": "趋势样本不足",
        }
    cur = rows[-1]
    cur_ts = int(cur.get("ts") or 0)
    # 主要看最近 15 分钟；若没有更近的样本，就退回当前已保存的最早样本。
    candidates = [x for x in rows[:-1] if cur_ts - int(x.get("ts") or 0) <= 900]
    prev = candidates[0] if candidates else rows[0]
    elapsed = max(1, cur_ts - int(prev.get("ts") or cur_ts - 1))
    ds = int(cur.get("seeders") or 0) - int(prev.get("seeders") or 0)
    dl = int(cur.get("leechers") or 0) - int(prev.get("leechers") or 0)
    rate = dl / (elapsed / 60.0)
    rising = dl >= 2 and rate >= 0.8
    sign_s = "+" if ds >= 0 else ""
    sign_l = "+" if dl >= 0 else ""
    return {
        "samples": len(rows),
        "elapsed_seconds": elapsed,
        "delta_seeders": ds,
        "delta_leechers": dl,
        "leecher_per_minute": round(rate, 2),
        "rising": rising,
        "summary": f"趋势 ΔS {sign_s}{ds} / ΔL {sign_l}{dl} · {rate:.1f}L/min",
    }


def required_score_for_size(size_gb: float, base_score: float, age_seconds: int) -> float:
    size = max(0.0, float(size_gb or 0))
    base = float(base_score or 65)
    if size <= 2.0:
        req = base - 10
    elif size <= 4.0:
        req = base - 3
    elif size <= 6.0:
        req = base + 5
    else:
        req = base + 15
    if age_seconds > 1800:
        req += 10
    elif age_seconds > 900:
        req += 5
    return round(max(40.0, req), 1)


def dynamic_requirements(age_seconds: int, cfg: dict) -> dict:
    age = max(0, int(age_seconds or 0))
    base_l = max(0, _i(cfg.get("min_leechers"), 4))
    base_d = max(0.0, _f(cfg.get("min_demand"), 0.5))
    if age <= 90:
        leechers = max(1, min(base_l or 2, 2))
        demand = min(base_d or 0.5, 0.35)
    elif age <= 300:
        leechers = max(2, base_l)
        demand = max(0.4, base_d)
    elif age <= 900:
        leechers = max(5, base_l)
        demand = max(0.65, base_d)
    elif age <= 1800:
        leechers = max(8, base_l * 2)
        demand = max(1.0, base_d)
    else:
        leechers = max(15, base_l * 3)
        demand = max(1.5, base_d)
    return {"min_leechers": leechers, "min_demand": round(demand, 2)}


def _age_points(age: int, soft_age: int) -> tuple:
    if age <= 60:
        return 46.0, f"极新 {age}s"
    if age <= 120:
        return 40.0, f"很新 {age}s"
    if age <= 300:
        return 30.0, f"新种 {age}s"
    if age <= 600:
        return 20.0, f"早期 {age}s"
    if age <= soft_age:
        return 12.0, f"仍在黄金窗口 {age}s"
    if age <= 1800:
        return 2.0, f"晚起量观察 {age}s"
    return -8.0, f"超晚救援 {age}s"


def evaluate_torrent(meta: dict, cfg: dict, history: list = None, now_dt: datetime = None) -> dict:
    """趋势感知的盒子抢流评分。

    时间和 Seeder 都是软因素；只有体积无效/越界、或超过 hard_max_age_seconds 才永久拒绝。
    """
    now_dt = now_dt or now()
    size_gb = _f(meta.get("size_gb"), 0)
    seeders = max(0, _i(meta.get("seeders"), 0))
    leechers = max(0, _i(meta.get("leechers"), 0))
    created = _parse_created(meta.get("created_date") or "")
    age = max(0, int((now_dt - created).total_seconds())) if created else 999999

    min_size = max(0.0, _f(cfg.get("min_size_gb"), 0.3))
    max_size = max(0.0, _f(cfg.get("max_size_gb"), 9.0))
    soft_age = max(300, _i(cfg.get("max_age_seconds"), 900))
    hard_age = max(soft_age, _i(cfg.get("hard_max_age_seconds"), 3600))
    base_score = _f(cfg.get("min_score"), 65.0)
    soft_seeders = max(3, _i(cfg.get("max_seeders"), 25))

    if size_gb <= 0:
        return {"accepted": False, "permanent": True, "score": 0.0, "priority": -999.0, "age_seconds": age, "demand": 0.0, "reasons": ["无有效体积"]}
    if min_size and size_gb < min_size:
        return {"accepted": False, "permanent": True, "score": 0.0, "priority": -999.0, "age_seconds": age, "demand": 0.0, "reasons": [f"体积过小 {size_gb:.2f}GB"]}
    if max_size and size_gb > max_size:
        return {"accepted": False, "permanent": True, "score": 0.0, "priority": -999.0, "age_seconds": age, "demand": 0.0, "reasons": [f"体积过大 {size_gb:.2f}GB"]}
    if age > hard_age:
        return {"accepted": False, "permanent": True, "score": 0.0, "priority": -999.0, "age_seconds": age, "demand": 0.0, "reasons": [f"超过绝对观察上限 {age}s>{hard_age}s"]}

    reasons = []
    score, age_reason = _age_points(age, soft_age)
    reasons.append(age_reason)

    demand = leechers / (seeders + 1)
    score += min(42.0, demand * 12.0)
    score += min(30.0, leechers * 1.5)

    # Seeder 不再一刀切。超过参考上限只意味着竞争更激烈。
    seed_penalty = min(28.0, max(0, seeders - 3) * 0.55)
    if seeders > soft_seeders:
        seed_penalty += min(12.0, (seeders - soft_seeders) * 0.35)
        reasons.append(f"竞争偏高 S={seeders}>{soft_seeders}")
    score -= seed_penalty

    if size_gb <= 2.0:
        score += 10
        reasons.append(f"轻量甜点 {size_gb:.2f}GB")
    elif size_gb <= 4.0:
        score += 7
        reasons.append(f"甜点体积 {size_gb:.2f}GB")
    elif size_gb <= 6.0:
        score += 2
        reasons.append(f"中等体积 {size_gb:.2f}GB")
    else:
        score -= 4
        reasons.append(f"大种成本 {size_gb:.2f}GB")

    trend = trend_stats(history or [])
    trend_bonus = 0.0
    if trend["samples"] >= 2:
        dl = trend["delta_leechers"]
        rate = trend["leecher_per_minute"]
        if dl > 0:
            trend_bonus += min(18.0, dl * 2.5)
        if rate > 1:
            trend_bonus += min(10.0, (rate - 1) * 2.0)
        if dl <= 0 and age > 300:
            trend_bonus -= 5
        score += trend_bonus
        reasons.append(trend["summary"])

    req = dynamic_requirements(age, cfg)
    required_score = required_score_for_size(size_gb, base_score, age)
    reasons.append(f"S/L={seeders}/{leechers}")
    reasons.append(f"需求比={demand:.2f}")

    temporary = False
    if leechers < req["min_leechers"]:
        temporary = True
        reasons.append(f"当前阶段下载者不足 {leechers}<{req['min_leechers']}")
    if demand < req["min_demand"]:
        temporary = True
        reasons.append(f"当前阶段需求比不足 {demand:.2f}<{req['min_demand']:.2f}")
    if score < required_score:
        temporary = True
        reasons.append(f"动态门槛不足 {score:.1f}<{required_score:.1f}")

    priority = score - required_score + min(10.0, demand * 2.0) + max(0.0, trend_bonus * 0.35)
    return {
        "accepted": not temporary,
        "permanent": False,
        "score": round(score, 1),
        "priority": round(priority, 1),
        "required_score": required_score,
        "required_leechers": req["min_leechers"],
        "required_demand": req["min_demand"],
        "age_seconds": age,
        "demand": round(demand, 3),
        "trend": trend,
        "reasons": reasons,
    }


def watch_retry_delay(age_seconds: int, trend: dict = None) -> int:
    age = max(0, int(age_seconds or 0))
    if age < 90:
        delay = 45
    elif age < 300:
        delay = 60
    elif age < 900:
        delay = 120
    elif age < 1800:
        delay = 180
    else:
        delay = 300
    if (trend or {}).get("rising"):
        delay = max(30, delay // 2)
    return delay
