import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import box_mteam


class BoxApiBudgetTests(unittest.TestCase):
    def test_rolling_window_usage_releases_old_calls(self):
        usage = box_mteam.rolling_window_usage([1000, 1200, 1500], 3, now_ts=4501)
        self.assertEqual(usage["used"], 0)
        self.assertEqual(usage["remaining"], 3)

    def test_budget_blocks_at_limit_and_releases_after_hour(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "box_api_budget.json"
            with patch.object(box_mteam, "BOX_API_BUDGET_FILE", path):
                first = box_mteam.reserve_api_call("detail", 2, now_ts=1000)
                second = box_mteam.reserve_api_call("detail", 2, now_ts=1100)
                self.assertEqual(first["used"], 1)
                self.assertEqual(second["used"], 2)
                with self.assertRaises(box_mteam.BoxApiBudgetError):
                    box_mteam.reserve_api_call("detail", 2, now_ts=1200)
                released = box_mteam.reserve_api_call("detail", 2, now_ts=4701)
                self.assertEqual(released["used"], 1)

    def test_detail_and_download_budgets_are_independent_and_persistent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "box_api_budget.json"
            with patch.object(box_mteam, "BOX_API_BUDGET_FILE", path):
                box_mteam.reserve_api_call("detail", 3, now_ts=2000)
                box_mteam.reserve_api_call("detail", 3, now_ts=2001)
                box_mteam.reserve_api_call("download", 2, now_ts=2002)
                snap = box_mteam.box_api_budget_snapshot(
                    {"detail_limit_per_hour": 3, "download_limit_per_hour": 2},
                    now_ts=2003,
                )
                self.assertEqual(snap["detail"]["used"], 2)
                self.assertEqual(snap["detail"]["remaining"], 1)
                self.assertEqual(snap["download"]["used"], 1)
                self.assertEqual(snap["download"]["remaining"], 1)
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
