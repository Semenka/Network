import json
import tempfile
import unittest
from pathlib import Path

from network_chief.db import connect, init_db
from network_chief.importers.gmail import import_gmail_json


class GmailImporterTest(unittest.TestCase):
    def test_import_gmail_json_skips_mailbox_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "gmail.json"
            json_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "m1",
                            "from": "Alice <alice@example.com>",
                            "to": "Me <me@example.com>",
                            "date": "2026-04-27T09:00:00Z",
                            "subject": "Hello",
                            "snippet": "Nice to meet you",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            con = connect(":memory:")
            init_db(con)

            stats = import_gmail_json(con, json_path, mailbox_owner="me@example.com")

            self.assertEqual(stats["people_seen"], 1)
            person = con.execute("SELECT * FROM people").fetchone()
            self.assertEqual(person["primary_email"], "alice@example.com")
            interaction = con.execute("SELECT * FROM interactions").fetchone()
            self.assertEqual(interaction["direction"], "incoming")

            stats_again = import_gmail_json(con, json_path, mailbox_owner="me@example.com")
            self.assertEqual(stats_again["people_seen"], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM interactions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
