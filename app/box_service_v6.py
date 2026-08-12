import time

from app.box_config import load_box_config, load_box_state, save_box_state
from app.box_experiment import experiment_summary, register_experiment, sync_experiments
from app.box_mteam import BoxApiBudgetError, box_api_budget_snapshot, box_torrent_bytes, box_torrent_detail
from app.box_service import _parse_created, _safe_filename, disk_snapshot, fetch_rss, traffic_snapshot
from app.box_service_v2 import is_junk_rss_title, rss_source_fingerprint
from app.box_service_v5 import BoxControllerV5
from app.mteam import client as mteam_client
from app.qbittorrent import QBittorrentClient
from app.store import append_download_log
from app.timeutil import now


def race_age_limit_for_size(size_gb: float, cfg: dict) -> int:
    """返回该体积在 Race Lane 中允许的最大种龄；0 表示不走 Race。"""
    size = max(0.0, float(size_gb or 0))
    minimum = max(0.0, float(cfg.get("race_min_size_gb") or 0.3))
    if size < minimum:
        return 0

    tiers = (
        (float(cfg.get("race_tier1_max_size_gb") or 2.0), int(cfg.get("race_tier1_max_age_seconds") or 180)),
        (float(cfg.get("race_tier2_max_size_gb") or 4.0), int(cfg.get("race_tier2_max_age_seconds") or 150)),
        (float(cfg.get("race_tier3_max_size_gb") or 6.0), int(cfg.get("race_tier3_max_age_seconds") or 90)),
    )
    global_max = float(cfg.get("max_size_gb") or 0)
    if global_max > 0 and size > global_max:
        return 0
    for max_size, max_age in tiers:
        if size <= max_size:
            return max(20, max_age)
    return 0


class BoxControllerV6(BoxControllerV5):
    """V5 + tiered Race Lane + race/trend 战绩对照。"""

    def _collect_experiments(self, cfg: dict = None) -> dict:
        cfg = cfg or load_box_config()
        if not cfg.get("experiment_enabled", True):
            return {"race": {}, "trend": {}}
        try:
            qb = QBittorrentClient(cfg)
            qitems = qb.torrents(tagged_only=True)
            state = load_box_state()
            experiments = sync_experiments(state, qitems, int(time.time()))
            save_box_state(state)
            return experiment_summary(experiments)
        except Exception:
            state = load_box_state()
            return experiment_summary(state.get("experiments") or {})

    @staticmethod
    def _identify_new_hash(before: list, after: list) -> str:
        old = {str(x.get("hash") or "") for x in (before or []) if x.get("hash")}
        fresh = [x for x in (after or []) if x.get("hash") and str(x.get("hash")) not in old]
        if not fresh:
            return ""
        fresh.sort(key=lambda x: int(x.get("added_on") or 0), reverse=True)
        return str(fresh[0].get("hash") or "")

    def _try_race_lane(self, cfg: dict) -> dict:
        if not cfg.get("race_lane_enabled", True):
            return {"enabled": False, "added": [], "checked": 0, "reason": "Race Lane 已关闭"}

        state = load_box_state()
        rss_url = str(cfg.get("rss_url") or "").strip()
        if not rss_url or not state.get("rss_warmed_up"):
            return {"enabled": True, "added": [], "checked": 0, "reason": "RSS 尚未暖机"}
        if state.get("rss_source_fp") != rss_source_fingerprint(rss_url):
            return {"enabled": True, "added": [], "checked": 0, "reason": "RSS 地址已变化，等待重新暖机"}

        qb = QBittorrentClient(cfg)
        qitems = qb.torrents(tagged_only=True)
        summary = QBittorrentClient.summary(qitems)
        max_active = max(1, int(cfg.get("max_active_downloads") or 1))
        if int(summary.get("active_downloads") or 0) >= max_active:
            return {"enabled": True, "added": [], "checked": 0, "reason": "当前下载槽已占用"}

        traffic = traffic_snapshot(cfg, state=state, persist=True)
        if traffic.get("hard_stop_reached") or traffic.get("budget_reached"):
            return {"enabled": True, "added": [], "checked": 0, "reason": "当前流量预算不允许 Race 入场"}
        state = load_box_state()
        disk = disk_snapshot(cfg)

        feed = fetch_rss(rss_url)
        seen = set(str(x) for x in (state.get("seen_ids") or []))
        retry_at = state.get("watch_retry_at") or {}
        observations = state.get("torrent_observations") or {}
        max_probe = max(1, min(20, int(cfg.get("race_candidates_per_run") or 6)))

        api = box_api_budget_snapshot(cfg)
        detail_remaining = int((api.get("detail") or {}).get("remaining") or 0)
        download_remaining = int((api.get("download") or {}).get("remaining") or 0)
        if detail_remaining <= 0 or download_remaining <= 0:
            return {"enabled": True, "added": [], "checked": 0, "reason": "Race API 配额暂不可用"}

        probed = 0
        eligible = []
        for item in (feed.get("items") or []):
            if probed >= max_probe or detail_remaining <= 0:
                break
            tid = str(item.get("id") or "")
            if not tid or tid in seen:
                continue
            # 已经进入 Trend/watch 的旧候选不再让 Race 重复探测；Race 只抢第一次出现的新 ID。
            if tid in retry_at or tid in observations:
                continue
            title = str(item.get("title") or "")
            if is_junk_rss_title(title):
                continue

            probed += 1
            detail_remaining -= 1
            try:
                detail = box_torrent_detail(tid, cfg)
                meta = mteam_client._torrent_meta(detail)
            except BoxApiBudgetError:
                break
            except Exception:
                continue

            size_gb = float(meta.get("size_gb") or 0)
            created = _parse_created(meta.get("created_date") or "")
            age_seconds = max(0, int((now() - created).total_seconds())) if created else 999999
            allowed_age = race_age_limit_for_size(size_gb, cfg)
            if allowed_age <= 0 or age_seconds > allowed_age:
                continue
            eligible.append({
                "tid": tid,
                "title": title,
                "meta": meta,
                "age_seconds": age_seconds,
                "allowed_age": allowed_age,
                "size_gb": size_gb,
            })

        if not eligible:
            return {
                "enabled": True,
                "added": [],
                "checked": probed,
                "reason": "本轮没有命中 Race 分档年龄×体积条件的新种",
            }

        eligible.sort(key=lambda x: (int(x.get("age_seconds") or 999999), float(x.get("size_gb") or 999)))
        candidate = eligible[0]
        tid = candidate["tid"]
        meta = candidate["meta"]
        age_seconds = candidate["age_seconds"]
        allowed_age = candidate["allowed_age"]
        size_gb = candidate["size_gb"]
        seeders = int(meta.get("seeders") or 0)
        leechers = int(meta.get("leechers") or 0)

        allowed, why = self._resource_gate(meta, cfg, qitems, traffic, disk)
        if not allowed:
            self._decision(state, {
                "torrent_id": tid,
                "name": meta.get("name") or candidate["title"] or tid,
                "result": "race-blocked",
                "size_gb": size_gb,
                "seeders": seeders,
                "leechers": leechers,
                "age_seconds": age_seconds,
                "score": 0,
                "reason": f"Race Lane 已选中最新种，但资源暂不可用：{why}",
            })
            save_box_state(state)
            return {"enabled": True, "added": [], "checked": probed, "reason": why, "candidate": tid}

        try:
            before = list(qitems)
            content = box_torrent_bytes(tid, cfg)
            qb.add_torrent(content, _safe_filename(tid, meta.get("name") or tid))
            after = qb.torrents(tagged_only=True)
            qbit_hash = self._identify_new_hash(before, after)

            state = load_box_state()
            seen = set(str(x) for x in (state.get("seen_ids") or []))
            seen.add(tid)
            state["seen_ids"] = list(seen)
            state.setdefault("watch_retry_at", {}).pop(tid, None)
            state.setdefault("torrent_observations", {}).pop(tid, None)
            state.setdefault("resource_wait_queue", {}).pop(tid, None)

            name = meta.get("name") or candidate["title"] or tid
            row = {
                "torrent_id": tid,
                "name": name,
                "result": "added-race",
                "size_gb": size_gb,
                "seeders": seeders,
                "leechers": leechers,
                "age_seconds": age_seconds,
                "demand": round(leechers / (seeders + 1), 3),
                "score": 0,
                "priority": 999,
                "reason": (
                    f"Race Lane：命中分档直接入场，不等待 Leecher/需求比/评分；"
                    f"入场 {age_seconds}s/{allowed_age}s · {size_gb:.2f}GB · S/L={seeders}/{leechers}"
                ),
            }
            self._decision(state, row)
            if cfg.get("experiment_enabled", True):
                register_experiment(
                    state,
                    torrent_id=tid,
                    name=name,
                    strategy="race",
                    qbit_hash=qbit_hash,
                    added_at=int(time.time()),
                    size_gb=size_gb,
                    entry_age_seconds=age_seconds,
                    entry_seeders=seeders,
                    entry_leechers=leechers,
                    entry_score=0,
                )
            save_box_state(state)
            append_download_log(
                f"Race Lane 秒冲 id={tid} age={age_seconds}/{allowed_age}s size={size_gb:.2f}GB S/L={seeders}/{leechers}",
                action="box_add_race",
                torrent_id=tid,
                name=name,
                strategy="race",
                qbit_hash=qbit_hash,
                entry_age_seconds=age_seconds,
                size_gb=size_gb,
                seeders=seeders,
                leechers=leechers,
            )
            return {
                "enabled": True,
                "added": [tid],
                "checked": probed,
                "candidate": tid,
                "age_seconds": age_seconds,
                "allowed_age": allowed_age,
                "size_gb": round(size_gb, 2),
            }
        except BoxApiBudgetError as e:
            return {"enabled": True, "added": [], "checked": probed, "reason": str(e), "candidate": tid}
        except Exception as e:
            self._decision(state, {
                "torrent_id": tid,
                "name": meta.get("name") or candidate["title"] or tid,
                "result": "race-error",
                "reason": str(e)[:300],
            })
            save_box_state(state)
            return {"enabled": True, "added": [], "checked": probed, "reason": str(e)[:300], "candidate": tid}

    def run_once(self) -> dict:
        cfg = load_box_config()
        before_summary = self._collect_experiments(cfg)
        pre_race_cleanup = self.cleanup() if cfg.get("auto_cleanup") else {"deleted": []}
        race = self._try_race_lane(cfg)
        result = super().run_once()
        after_summary = self._collect_experiments(cfg)
        if isinstance(result, dict):
            result["race_lane"] = race
            result["race_pre_cleanup"] = pre_race_cleanup
            result["experiment"] = after_summary or before_summary
        return result

    def status(self) -> dict:
        out = super().status()
        cfg = load_box_config()
        state = load_box_state()
        out["race_lane"] = {
            "enabled": bool(cfg.get("race_lane_enabled", True)),
            "min_size_gb": float(cfg.get("race_min_size_gb") or 0.3),
            "tiers": [
                {
                    "max_size_gb": float(cfg.get("race_tier1_max_size_gb") or 2.0),
                    "max_age_seconds": int(cfg.get("race_tier1_max_age_seconds") or 180),
                },
                {
                    "max_size_gb": float(cfg.get("race_tier2_max_size_gb") or 4.0),
                    "max_age_seconds": int(cfg.get("race_tier2_max_age_seconds") or 150),
                },
                {
                    "max_size_gb": float(cfg.get("race_tier3_max_size_gb") or 6.0),
                    "max_age_seconds": int(cfg.get("race_tier3_max_age_seconds") or 90),
                },
            ],
            "candidates_per_run": int(cfg.get("race_candidates_per_run") or 6),
        }
        out["experiment"] = experiment_summary(state.get("experiments") or {})
        return out


controller = BoxControllerV6()
