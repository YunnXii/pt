import unittest

from app.box_experiment import experiment_summary, register_experiment, sync_experiments
from app.box_service_v6 import BoxControllerV6


GIB = 1024 ** 3


class BoxExperimentTests(unittest.TestCase):
    def test_register_race_and_capture_milestones(self):
        state = {"experiments": {}, "decisions": []}
        register_experiment(
            state,
            torrent_id="123",
            name="Tiny.Hot.Show.S01E01",
            strategy="race",
            qbit_hash="abc",
            added_at=1000,
            size_gb=1.0,
            entry_age_seconds=30,
            entry_seeders=1,
            entry_leechers=0,
        )
        qitems = [{
            "hash": "abc",
            "name": "Tiny.Hot.Show.S01E01",
            "added_on": 1000,
            "size": 1 * GIB,
            "downloaded": 1 * GIB,
            "uploaded": 2 * GIB,
            "ratio": 2.0,
            "progress": 1.0,
            "upspeed": 1024,
            "dlspeed": 0,
            "state": "uploading",
        }]
        rows = sync_experiments(state, qitems, now_ts=1310)
        row = rows["abc"]
        self.assertIn("m1", row["milestones"])
        self.assertIn("m3", row["milestones"])
        self.assertIn("m5", row["milestones"])
        self.assertNotIn("m10", row["milestones"])
        self.assertEqual(row["latest"]["ratio"], 2.0)

    def test_trend_tasks_can_be_backfilled_from_decisions(self):
        state = {
            "experiments": {},
            "decisions": [{
                "torrent_id": "77",
                "name": "My Show 1080p WEB-DL",
                "result": "added",
                "age_seconds": 380,
                "seeders": 10,
                "leechers": 100,
                "score": 90,
            }],
        }
        qitems = [{
            "hash": "hash77",
            "name": "My.Show.1080p.WEB-DL",
            "added_on": 1000,
            "size": 2 * GIB,
            "downloaded": 2 * GIB,
            "uploaded": 3 * GIB,
            "ratio": 1.5,
            "progress": 1.0,
            "upspeed": 0,
            "dlspeed": 0,
            "state": "uploading",
        }]
        rows = sync_experiments(state, qitems, now_ts=1600)
        row = rows["hash77"]
        self.assertEqual(row["strategy"], "trend")
        self.assertEqual(row["entry_age_seconds"], 380)
        self.assertEqual(row["entry_leechers"], 100)

    def test_summary_compares_race_and_trend(self):
        experiments = {
            "a": {"strategy": "race", "latest": {"ratio": 2.5, "progress": 1, "uploaded": 250, "downloaded": 100}},
            "b": {"strategy": "race", "latest": {"ratio": 0.5, "progress": 1, "uploaded": 50, "downloaded": 100}},
            "c": {"strategy": "trend", "latest": {"ratio": 1.5, "progress": 1, "uploaded": 150, "downloaded": 100}},
        }
        s = experiment_summary(experiments)
        self.assertEqual(s["race"]["count"], 2)
        self.assertEqual(s["race"]["avg_ratio"], 1.5)
        self.assertEqual(s["race"]["ratio_ge_2_rate"], 50.0)
        self.assertEqual(s["trend"]["avg_ratio"], 1.5)

    def test_identify_new_hash_uses_newly_added_torrent(self):
        before = [{"hash": "old", "added_on": 100}]
        after = [
            {"hash": "old", "added_on": 100},
            {"hash": "new", "added_on": 200},
        ]
        self.assertEqual(BoxControllerV6._identify_new_hash(before, after), "new")


if __name__ == "__main__":
    unittest.main()
