import tempfile
import unittest
from pathlib import Path

from network_chief.db import connect, init_db
from network_chief.sync import discover_source_files, summarize_sync, sync_sources


class SyncSourcesTest(unittest.TestCase):
    def test_discover_source_files_finds_linkedin_and_gmail_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linkedin = root / "Basic_LinkedInDataExport_05-01-2026.zip"
            linkedin.mkdir()
            (linkedin / "Connections.csv").write_text(
                "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n",
                encoding="utf-8",
            )
            (root / "gmail.json").write_text("[]", encoding="utf-8")

            discovered = discover_source_files([root])

            self.assertEqual([path.name for path in discovered["linkedin_connections"]], ["Connections.csv"])
            self.assertEqual([path.name for path in discovered["gmail_json"]], ["gmail.json"])

    def test_sync_sources_imports_detected_exports_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linkedin = root / "Basic_LinkedInDataExport_05-01-2026.zip"
            linkedin.mkdir()
            (linkedin / "Connections.csv").write_text(
                "Metadata row\n"
                "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
                "Alice,Builder,https://linkedin.com/in/alice,alice@example.com,Builder Studio,AI Operator,2026-01-01\n",
                encoding="utf-8",
            )
            con = connect(":memory:")
            init_db(con)

            stats = sync_sources(con, scan_dirs=[root])
            report = summarize_sync(stats)

            self.assertEqual(stats["found"]["linkedin_connections"], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertIn("linkedin_connections: 1 file(s)", report)
            self.assertIn("Gmail MBOX or connector JSON was not found", report)


if __name__ == "__main__":
    unittest.main()
