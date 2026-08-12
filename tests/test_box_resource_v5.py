import unittest

from app.box_service_v5 import BoxControllerV5, logical_data_bytes, was_resource_blocked


GIB = 1024 ** 3


class BoxResourceV5Tests(unittest.TestCase):
    def setUp(self):
        self.controller = BoxControllerV5()
        self.cfg = {
            "max_active_downloads": 1,
            "data_cap_gb": 16.0,
            "disk_reserve_gb": 1.0,
        }
        self.traffic = {"budget_reached": False}
        self.disk = {"free_gb": 14.2}

    def test_zero_percent_large_torrent_does_not_consume_logical_data_cap(self):
        items = [
            {
                "size": 8 * GIB,
                "progress": 0.0,
                "state": "pausedDL",
                "downloaded": 0,
                "uploaded": 0,
                "dlspeed": 0,
            },
            {
                "size": 6 * GIB,
                "progress": 1.0,
                "state": "uploading",
                "downloaded": 6 * GIB,
                "uploaded": 10 * GIB,
                "dlspeed": 0,
            },
        ]
        self.assertEqual(logical_data_bytes(items), 6 * GIB)
        ok, why = self.controller._rough_resource_fit(
            {"size_gb": 3.16}, self.cfg, items, self.traffic, self.disk
        )
        self.assertTrue(ok, why)

    def test_completed_data_still_counts_against_cap(self):
        items = [
            {
                "size": 14 * GIB,
                "progress": 1.0,
                "state": "uploading",
                "downloaded": 14 * GIB,
                "uploaded": 20 * GIB,
                "dlspeed": 0,
            }
        ]
        ok, why = self.controller._rough_resource_fit(
            {"size_gb": 3.0}, self.cfg, items, self.traffic, self.disk
        )
        self.assertFalse(ok)
        self.assertIn("实际已占", why)

    def test_partial_data_counts_only_completed_fraction(self):
        items = [
            {
                "size": 8 * GIB,
                "progress": 0.25,
                "state": "pausedDL",
                "downloaded": 2 * GIB,
                "uploaded": 0,
                "dlspeed": 0,
            }
        ]
        self.assertEqual(logical_data_bytes(items), 2 * GIB)

    def test_old_data_cap_reason_is_recognized_for_immediate_wake(self):
        self.assertTrue(was_resource_blocked({"reason": "盒子数据上限 16.0GB"}))
        self.assertTrue(was_resource_blocked({"reason": "盒子数据上限仍不足"}))
        self.assertTrue(was_resource_blocked({"reason": "下载槽仍被占用"}))
        self.assertFalse(was_resource_blocked({"reason": "实时需求重新评估后暂不值得下载"}))


if __name__ == "__main__":
    unittest.main()
