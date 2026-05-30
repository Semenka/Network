import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from network_chief import cli


class AutoRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "network.db")
        self.dash_dir = Path(self.tmp.name) / "dashboards"
        self.env = mock.patch.dict(
            os.environ,
            {
                "NETWORK_CHIEF_DB": self.db_path,
                "NETWORK_CHIEF_DASHBOARDS_DIR": str(self.dash_dir),
            },
        )
        self.env.start()
        # init the schema
        cli.main(["init"])

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_state_changing_command_refreshes_dashboard(self) -> None:
        # Use add-goal — a small state-changing command that needs no fixtures.
        rc = cli.main([
            "add-goal", "--title", "X", "--cadence", "weekly",
        ])
        self.assertEqual(rc, 0)
        out_md = self.dash_dir / "dashboard-30d.md"
        out_json = self.dash_dir / "dashboard-30d.json"
        self.assertTrue(out_md.exists(), "auto-refresh should have written dashboard markdown")
        self.assertTrue(out_json.exists(), "auto-refresh should have written dashboard json")
        content = out_md.read_text()
        self.assertIn("# Network Chief Dashboard (30-day window)", content)
        self.assertIn("```mermaid", content)

    def test_no_dashboard_flag_suppresses_refresh(self) -> None:
        rc = cli.main([
            "--no-dashboard",
            "add-goal", "--title", "Y", "--cadence", "weekly",
        ])
        self.assertEqual(rc, 0)
        self.assertFalse((self.dash_dir / "dashboard-30d.md").exists())

    def test_env_var_suppresses_refresh(self) -> None:
        with mock.patch.dict(os.environ, {"NETWORK_CHIEF_NO_DASHBOARD": "1"}):
            rc = cli.main(["add-goal", "--title", "Z", "--cadence", "weekly"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.dash_dir / "dashboard-30d.md").exists())

    def test_read_only_command_skips_refresh(self) -> None:
        # 'goals' is read-only and not in the state-changing set.
        rc = cli.main(["goals", "--all"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.dash_dir / "dashboard-30d.md").exists())


if __name__ == "__main__":
    unittest.main()
