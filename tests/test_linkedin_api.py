import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from network_chief.db import connect, init_db
from network_chief.importers import linkedin_api


SAMPLE_CSV = Path(__file__).resolve().parent.parent / "examples" / "linkedin_connections_sample.csv"


class LinkedInDMAStubTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_dma_raises_clear_error(self) -> None:
        with mock.patch.dict("os.environ", {"LINKEDIN_DMA_ENABLED": ""}):
            with self.assertRaises(linkedin_api.LinkedInDMARequired) as ctx:
                linkedin_api.sync_linkedin_dma(self.con)
        self.assertIn("guided-export", str(ctx.exception))


class GuidedExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_picks_up_dropped_csv(self) -> None:
        target = self.tmp / "Connections.csv"

        def drop_file(*_a, **_kw):
            shutil.copy(SAMPLE_CSV, target)
            return True

        # mock webbrowser.open to drop the CSV at the same moment the page "opens".
        with mock.patch("network_chief.importers.linkedin_api.webbrowser.open", side_effect=drop_file):
            with mock.patch("time.sleep"):
                stats = linkedin_api.guided_linkedin_export(
                    self.con, watch_dir=self.tmp, timeout_s=2, open_browser=True
                )
        self.assertEqual(stats["status"], "ok")
        self.assertIsNotNone(stats["connections"])
        self.assertGreaterEqual(stats["connections"].get("people_seen", 0), 1)

    def test_times_out_cleanly_when_nothing_arrives(self) -> None:
        with mock.patch("network_chief.importers.linkedin_api.webbrowser.open", return_value=True):
            with mock.patch("time.sleep"):
                with mock.patch("time.time", side_effect=[0, 0, 0, 9999, 9999]):
                    stats = linkedin_api.guided_linkedin_export(
                        self.con, watch_dir=self.tmp, timeout_s=1, open_browser=True
                    )
        self.assertEqual(stats["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
