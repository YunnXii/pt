import unittest
from datetime import timedelta

from app.box_service import score_torrent
from app.timeutil import now


class BoxScoringTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "min_size_gb": 0.5,
            "max_size_gb": 6.0,
            "max_age_seconds": 600,
            "min_leechers": 1,
            "max_seeders": 50,
            "min_demand": 0.25,
            "min_score": 45,
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
        self.assertGreaterEqual(result["score"], 45)

    def test_old_torrent_is_permanent_reject(self):
        result = score_torrent(self.meta(age=1200), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["permanent"])

    def test_low_demand_is_watch_not_permanent(self):
        result = score_torrent(self.meta(seeders=20, leechers=1, age=60), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["permanent"])

    def test_oversize_is_permanent_reject(self):
        result = score_torrent(self.meta(size=8.0), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["permanent"])


if __name__ == "__main__":
    unittest.main()
