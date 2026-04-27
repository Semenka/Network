import tempfile
import unittest
from pathlib import Path

from network_chief.db import connect, init_db, list_goals
from network_chief.importers.linkedin import import_connections


class LinkedInImporterTest(unittest.TestCase):
    def test_import_linkedin_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "Connections.csv"
            csv_path.write_text(
                "Metadata row\n"
                "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
                "Alice,Investor,https://linkedin.com/in/alice,alice@example.com,Example Ventures,Partner,2025-02-01\n",
                encoding="utf-8",
            )
            con = connect(":memory:")
            init_db(con)

            stats = import_connections(con, csv_path)

            self.assertEqual(stats["people_seen"], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM roles").fetchone()[0], 1)
            self.assertEqual(
                con.execute("SELECT count(*) FROM resources WHERE resource_type = 'financial'").fetchone()[0],
                1,
            )
            self.assertEqual(list_goals(con), [])


if __name__ == "__main__":
    unittest.main()
