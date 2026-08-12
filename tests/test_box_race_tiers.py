import unittest

from app.box_service_v6 import race_age_limit_for_size


class BoxRaceTierTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "min_size_gb": 0.3,
            "max_size_gb": 9.0,
            "race_min_size_gb": 0.3,
            "race_tier1_max_size_gb": 2.0,
            "race_tier1_max_age_seconds": 180,
            "race_tier2_max_size_gb": 4.0,
            "race_tier2_max_age_seconds": 150,
            "race_tier3_max_size_gb": 6.0,
            "race_tier3_max_age_seconds": 90,
        }

    def test_default_tiers(self):
        self.assertEqual(race_age_limit_for_size(0.8, self.cfg), 180)
        self.assertEqual(race_age_limit_for_size(2.0, self.cfg), 180)
        self.assertEqual(race_age_limit_for_size(2.1, self.cfg), 150)
        self.assertEqual(race_age_limit_for_size(4.0, self.cfg), 150)
        self.assertEqual(race_age_limit_for_size(4.1, self.cfg), 90)
        self.assertEqual(race_age_limit_for_size(6.0, self.cfg), 90)
        self.assertEqual(race_age_limit_for_size(6.1, self.cfg), 0)

    def test_below_race_minimum_is_not_raced(self):
        self.assertEqual(race_age_limit_for_size(0.2, self.cfg), 0)

    def test_global_max_still_caps_race(self):
        cfg = dict(self.cfg)
        cfg["max_size_gb"] = 3.0
        self.assertEqual(race_age_limit_for_size(3.5, cfg), 0)

    def test_user_can_expand_tiers(self):
        cfg = dict(self.cfg)
        cfg.update({
            "race_tier1_max_size_gb": 3.0,
            "race_tier1_max_age_seconds": 240,
            "race_tier2_max_size_gb": 5.0,
            "race_tier2_max_age_seconds": 180,
            "race_tier3_max_size_gb": 8.0,
            "race_tier3_max_age_seconds": 120,
        })
        self.assertEqual(race_age_limit_for_size(2.8, cfg), 240)
        self.assertEqual(race_age_limit_for_size(4.8, cfg), 180)
        self.assertEqual(race_age_limit_for_size(7.5, cfg), 120)


if __name__ == "__main__":
    unittest.main()
