import unittest
from datetime import timedelta

from app.box_decision import (
    add_observation,
    evaluate_torrent,
    required_score_for_size,
    trend_stats,
    watch_retry_delay,
)
from app.timeutil import now


class BoxDecisionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "min_size_gb": 0.3,
            "max_size_gb": 9.0,
            "max_age_seconds": 900,
            "hard_max_age_seconds": 3600,
            "min_leechers": 4,
            "max_seeders": 25,
            "min_demand": 0.5,
            "min_score": 65,
        }

    def meta(self, *, size=1.8, seeders=2, leechers=8, age=90):
        created = now() - timedelta(seconds=age)
        return {
            "size_gb": size,
            "seeders": seeders,
            "leechers": leechers,
            "created_date": created.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def test_very_new_zero_leecher_is_watch(self):
        result = evaluate_torrent(self.meta(seeders=1, leechers=0, age=8), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["permanent"])
        self.assertLessEqual(result["required_leechers"], 2)

    def test_late_hot_torrent_can_be_rescued(self):
        # 22 分钟已经超过 900 秒黄金窗口，但 S4/L31 仍有非常强的真实需求。
        result = evaluate_torrent(self.meta(size=2.0, seeders=4, leechers=31, age=1320), self.cfg)
        self.assertTrue(result["accepted"], result)
        self.assertFalse(result["permanent"])
        self.assertGreater(result["demand"], 6)

    def test_high_seeders_are_penalty_not_permanent_reject(self):
        result = evaluate_torrent(self.meta(size=1.5, seeders=30, leechers=80, age=240), self.cfg)
        self.assertFalse(result["permanent"])
        self.assertGreater(result["score"], 0)
        self.assertTrue(any("竞争偏高" in x for x in result["reasons"]))

    def test_hard_age_is_the_real_permanent_cutoff(self):
        result = evaluate_torrent(self.meta(seeders=2, leechers=100, age=3700), self.cfg)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["permanent"])

    def test_rising_leecher_trend_boosts_score(self):
        ts = 1_700_000_000
        history = []
        history = add_observation(history, 1, 0, ts)
        history = add_observation(history, 2, 7, ts + 80)
        trend = trend_stats(history)
        self.assertTrue(trend["rising"])
        self.assertEqual(trend["delta_leechers"], 7)
        with_trend = evaluate_torrent(self.meta(seeders=2, leechers=7, age=120), self.cfg, history=history)
        without_trend = evaluate_torrent(self.meta(seeders=2, leechers=7, age=120), self.cfg, history=[])
        self.assertGreater(with_trend["score"], without_trend["score"])

    def test_small_torrents_need_less_score_than_large_ones(self):
        small = required_score_for_size(1.5, 65, 120)
        large = required_score_for_size(8.0, 65, 120)
        self.assertLess(small, large)
        self.assertEqual(small, 55)
        self.assertEqual(large, 80)

    def test_early_watch_rechecks_quickly(self):
        self.assertEqual(watch_retry_delay(30), 45)
        self.assertEqual(watch_retry_delay(150), 60)
        self.assertEqual(watch_retry_delay(600), 120)
        self.assertEqual(watch_retry_delay(150, {"rising": True}), 30)


if __name__ == "__main__":
    unittest.main()
