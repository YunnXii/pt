import unittest

from app.box_cleanup import plan_stalled_download_cleanup


class BoxCleanupTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "cleanup_stalled_zero_minutes": 15,
            "cleanup_stalled_partial_minutes": 30,
        }

    def test_zero_byte_stalled_download_is_deleted_after_grace(self):
        item = {
            "hash": "a",
            "state": "stalledDL",
            "progress": 0.0,
            "downloaded": 0,
            "dlspeed": 0,
            "added_on": 1000,
        }
        plan = plan_stalled_download_cleanup([item], self.cfg, {}, now_ts=1901)
        self.assertEqual(plan["delete"], ["a"])
        self.assertIn("0B", plan["reasons"]["a"])

    def test_zero_byte_stalled_download_gets_grace_period(self):
        item = {
            "hash": "a",
            "state": "stalledDL",
            "progress": 0.0,
            "downloaded": 0,
            "dlspeed": 0,
            "added_on": 1000,
        }
        plan = plan_stalled_download_cleanup([item], self.cfg, {}, now_ts=1800)
        self.assertEqual(plan["delete"], [])

    def test_partial_download_tracks_real_byte_progress(self):
        item = {
            "hash": "b",
            "state": "stalledDL",
            "progress": 0.4,
            "downloaded": 400,
            "dlspeed": 0,
            "added_on": 1000,
        }
        first = plan_stalled_download_cleanup([item], self.cfg, {}, now_ts=2000)
        self.assertEqual(first["delete"], [])

        still = plan_stalled_download_cleanup(
            [item], self.cfg, first["progress_state"], now_ts=3799
        )
        self.assertEqual(still["delete"], [])

        expired = plan_stalled_download_cleanup(
            [item], self.cfg, still["progress_state"], now_ts=3801
        )
        self.assertEqual(expired["delete"], ["b"])

    def test_partial_progress_resets_stall_clock(self):
        item = {
            "hash": "b",
            "state": "stalledDL",
            "progress": 0.4,
            "downloaded": 400,
            "dlspeed": 0,
            "added_on": 1000,
        }
        first = plan_stalled_download_cleanup([item], self.cfg, {}, now_ts=2000)
        item2 = dict(item, downloaded=500, progress=0.5)
        second = plan_stalled_download_cleanup(
            [item2], self.cfg, first["progress_state"], now_ts=3700
        )
        self.assertEqual(second["delete"], [])
        self.assertEqual(second["progress_state"]["b"]["last_progress_at"], 3700)

    def test_paused_and_active_downloads_are_not_deleted(self):
        paused = {
            "hash": "p",
            "state": "pausedDL",
            "progress": 0.0,
            "downloaded": 0,
            "dlspeed": 0,
            "added_on": 1,
        }
        active = {
            "hash": "d",
            "state": "downloading",
            "progress": 0.2,
            "downloaded": 200,
            "dlspeed": 1024,
            "added_on": 1,
        }
        plan = plan_stalled_download_cleanup([paused, active], self.cfg, {}, now_ts=10000)
        self.assertEqual(plan["delete"], [])


if __name__ == "__main__":
    unittest.main()
