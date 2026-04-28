import tempfile
import unittest
from pathlib import Path

from network_chief.db import connect, init_db, list_goals
from network_chief.importers.linkedin import import_connections, import_linkedin_interactions


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

    def test_import_linkedin_interactions(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "LinkedInMessages.csv"
            csv_path.write_text(
                "From,To,Date,Subject,Content,Profile URL\n"
                "Andrey Semenov,Alice Investor,2026-04-27T09:00:00Z,AI capital,Can you introduce an AI investor?,https://linkedin.com/in/alice\n",
                encoding="utf-8",
            )
            con = connect(":memory:")
            init_db(con)

            stats = import_linkedin_interactions(con, csv_path, owner_name="Andrey Semenov")

            self.assertEqual(stats["people_seen"], 1)
            self.assertEqual(stats["interactions_seen"], 1)
            interaction = con.execute("SELECT * FROM interactions").fetchone()
            self.assertEqual(interaction["direction"], "outgoing")
            self.assertEqual(interaction["channel"], "linkedin")
            self.assertGreaterEqual(
                con.execute("SELECT count(*) FROM connection_values WHERE value_type = 'financial_capital'").fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
