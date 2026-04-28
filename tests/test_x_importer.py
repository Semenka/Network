import json
import tempfile
import unittest
from pathlib import Path

from network_chief.db import connect, init_db
from network_chief.importers.x import import_x_export


class XImporterTest(unittest.TestCase):
    def test_import_x_profiles_and_tweet_mentions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "handle": "@aliceai",
                            "name": "Alice AI",
                            "bio": "AI investor and automation operator.",
                            "interaction_type": "reply",
                            "text": "Happy to share specific knowledge on machine learning.",
                            "date": "2026-04-27T09:00:00Z",
                        },
                        {
                            "tweet": {
                                "id_str": "t1",
                                "created_at": "2026-04-28T10:00:00Z",
                                "full_text": "Thanks @builderbob for the product insight.",
                                "entities": {
                                    "user_mentions": [
                                        {"screen_name": "builderbob", "name": "Bob Builder"},
                                    ]
                                },
                            }
                        },
                    ]
                ),
                encoding="utf-8",
            )
            con = connect(":memory:")
            init_db(con)

            stats = import_x_export(con, path, owner_handle="andrey")

            self.assertEqual(stats["people_seen"], 2)
            self.assertEqual(stats["interactions_seen"], 2)
            self.assertGreaterEqual(stats["values_seen"], 2)
            handles = {row["twitter_handle"] for row in con.execute("SELECT twitter_handle FROM people")}
            self.assertEqual(handles, {"aliceai", "builderbob"})


if __name__ == "__main__":
    unittest.main()
