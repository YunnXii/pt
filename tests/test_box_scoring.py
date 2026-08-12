import unittest
from datetime import timedelta

from app.box_service import score_torrent
from app.box_service_v2 import is_junk_rss_title, watch_retry_delay
from app.timeutil import now


class BoxScoringTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "min_size_gb": 0.3,
            "max_size_gb": 9.0,
            "max_age_seconds": 900,
            "min_leechers": 4,
            "max_seeders": 25,
            "min_demand": 0.5,
            "min_score": 65,
        }

    def meta(self, *, size=3.0, seeders=3, leechers=20, age=45):
        created = now() - timedelta(seconds=age)
        return {
            "size_gb": size,
            "seeders": seeders,
            "leechers": leechers,
            "created_date": created.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def test_hot_new_small_torrent_is_accepted(self):
        result = score_torrent(self.meta(), self.cfg)
        self.assertTrue(result["accepted"])
        self.assertGreaterEqual(result["score"], 65)

    def test_old_torrent_is_permanent_reject(self):
        result = score_torrent(self.meta(age=1200), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["permanent"])

    def test_low_demand_is_watch_not_permanent(self):
        result = score_torrent(self.meta(seeders=20, leechers=1, age=60), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["permanent"])

    def test_oversize_is_permanent_reject(self):
        result = score_torrent(self.meta(size=12.0), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["permanent"])

    def test_known_bad_rss_title_is_filtered_before_detail(self):
        self.assertTrue(is_junk_rss_title("错误种子请删除"))
        self.assertTrue(is_junk_rss_title("Error Torrent Please Delete"))
        self.assertFalse(is_junk_rss_title("Trying S05E06 1080p WEB-DL"))

    def test_watch_retry_gets_slower_as_torrent_ages(self):
        self.assertEqual(watch_retry_delay(30), 120)
        self.assertEqual(watch_retry_delay(180), 180)
        self.assertEqual(watch_retry_delay(500), 300)


if __name__ == "__main__":
    unittest.main()
