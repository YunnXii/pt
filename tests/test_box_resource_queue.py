import unittest

from app.box_service_v4 import (
    BoxControllerV4,
    _latest_wait_resource_rows,
    _queue_sort_key,
)


class BoxResourceQueueTests(unittest.TestCase):
    def setUp(self):
        self.controller = BoxControllerV4()
        self.cfg = {
            "resource_queue_recheck_seconds": 60,
            "resource_queue_max_items": 120,
            "max_active_downloads": 1,
            "data_cap_gb": 16.0,
            "disk_reserve_gb": 4.0,
        }

    def test_latest_wait_resource_is_recovered(self):
        decisions = [
            {"torrent_id": "1", "result": "watch", "score": 50},
            {"torrent_id": "1", "result": "wait-resource", "score": 109, "size_gb": 3.16},
        ]
        rows = _latest_wait_resource_rows(decisions)
        self.assertIn("1", rows)
        self.assertEqual(rows["1"]["score"], 109)

    def test_old_wait_resource_does_not_resurrect_after_added(self):
        decisions = [
            {"torrent_id": "1", "result": "wait-resource", "score": 109},
            {"torrent_id": "1", "result": "added-resource-retry", "score": 105},
        ]
        self.assertNotIn("1", _latest_wait_resource_rows(decisions))

    def test_sync_migrates_wait_resource_immediately_and_delays_rss_duplicate(self):
        state = {
            "seen_ids": [],
            "resource_wait_queue": {},
            "watch_retry_at": {},
            "decisions": [
                {
                    "torrent_id": "99",
                    "name": "Hot Torrent",
                    "result": "wait-resource",
                    "score": 112,
                    "priority": 62,
                    "size_gb": 0.6,
                    "reason": "盒子数据上限 16.0GB",
                }
            ],
        }
        added = self.controller._sync_resource_queue_from_decisions(state, self.cfg, 1000)
        self.assertEqual(added, 1)
        self.assertIn("99", state["resource_wait_queue"])
        # 独立资源队列首次迁移必须本轮立即可复查。
        self.assertEqual(state["resource_wait_queue"]["99"]["next_retry_at"], 1000)
        # 但主 RSS 仍需退避，避免同一只 torrent 被两条路径重复查询。
        self.assertGreaterEqual(state["watch_retry_at"]["99"], 1060)

    def test_existing_queue_keeps_future_retry(self):
        state = {
            "seen_ids": [],
            "resource_wait_queue": {
                "99": {
                    "name": "Hot Torrent",
                    "first_wait_at": 900,
                    "last_wait_at": 950,
                    "next_retry_at": 1030,
                    "score": 112,
                    "priority": 62,
                    "size_gb": 0.6,
                }
            },
            "watch_retry_at": {},
            "decisions": [
                {
                    "torrent_id": "99",
                    "name": "Hot Torrent",
                    "result": "wait-resource",
                    "score": 112,
                    "priority": 62,
                    "size_gb": 0.6,
                }
            ],
        }
        added = self.controller._sync_resource_queue_from_decisions(state, self.cfg, 1000)
        self.assertEqual(added, 0)
        self.assertEqual(state["resource_wait_queue"]["99"]["next_retry_at"], 1030)

    def test_queue_priority_prefers_hotter_candidate(self):
        low = ("a", {"priority": 20, "score": 100, "size_gb": 1.0, "first_wait_at": 100})
        high = ("b", {"priority": 60, "score": 90, "size_gb": 5.0, "first_wait_at": 200})
        ordered = sorted([low, high], key=_queue_sort_key, reverse=True)
        self.assertEqual(ordered[0][0], "b")

    def test_rough_resource_fit_changes_when_space_frees(self):
        row = {"size_gb": 3.0}
        traffic = {"budget_reached": False}
        disk = {"free_gb": 10.0}
        full_items = [
            {"size": 14 * 1024**3, "downloaded": 0, "uploaded": 0, "progress": 1.0, "state": "uploading"}
        ]
        ok, _ = self.controller._rough_resource_fit(row, self.cfg, full_items, traffic, disk)
        self.assertFalse(ok)

        freed_items = [
            {"size": 10 * 1024**3, "downloaded": 0, "uploaded": 0, "progress": 1.0, "state": "uploading"}
        ]
        ok, why = self.controller._rough_resource_fit(row, self.cfg, freed_items, traffic, disk)
        self.assertTrue(ok, why)


if __name__ == "__main__":
    unittest.main()
