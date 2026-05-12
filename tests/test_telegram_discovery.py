import unittest

from network_chief.db import (
    add_interaction,
    add_source_fact,
    connect,
    init_db,
    upsert_person,
)
from network_chief.discovery import discover_telegram_handles, _extract


class TelegramExtractTest(unittest.TestCase):
    def test_high_confidence_t_me(self) -> None:
        self.assertEqual(_extract("Reach me at https://t.me/AlphaCoder"), {"alphacoder"})
        self.assertEqual(_extract("ping me — telegram.me/Maria_K"), {"maria_k"})

    def test_tg_resolve(self) -> None:
        self.assertEqual(_extract("tg://resolve?domain=DemoUser_42"), {"demouser_42"})

    def test_labeled_handle(self) -> None:
        self.assertEqual(_extract("Telegram: @samply_user"), {"samply_user"})
        self.assertEqual(_extract("TG: bobthebuilder"), {"bobthebuilder"})

    def test_short_or_invalid_ignored(self) -> None:
        self.assertEqual(_extract("@hi @abcd"), set())  # too short
        self.assertEqual(_extract("Sent via @gmail.com"), set())

    def test_blocklist_dropped(self) -> None:
        self.assertEqual(_extract("https://t.me/joinchat/AAAAAAAA"), set())


class DiscoverDBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_updates_only_empty_telegram_handle(self) -> None:
        # Person A: telegram in notes — should be filled in.
        a = upsert_person(self.con, full_name="A", notes="Tg: alphacoder")
        # Person B: telegram already set — should be skipped.
        b = upsert_person(self.con, full_name="B")
        self.con.execute("UPDATE people SET telegram_handle = 'preexisting' WHERE id = ?", (b,))
        self.con.commit()
        # Person C: telegram in source_facts.
        c = upsert_person(self.con, full_name="C")
        add_source_fact(
            self.con, person_id=c, fact_type="bio", fact_value="DM me at t.me/charlie_qa",
            source="seed", confidence=0.9,
        )
        # Person D: telegram in interaction body.
        d = upsert_person(self.con, full_name="D")
        add_interaction(
            self.con, person_id=d, channel="x", direction="incoming",
            subject="hi", body_summary="Drop me a msg on telegram: deltauser",
            source="seed", source_ref="t1",
        )
        # Person E: no telegram anywhere.
        upsert_person(self.con, full_name="E", notes="just a generic bio")

        stats = discover_telegram_handles(self.con)
        self.assertEqual(stats["scanned"], 5)
        self.assertGreaterEqual(stats["updated"], 3)

        handles = {row[0]: row[1] for row in self.con.execute(
            "SELECT id, telegram_handle FROM people"
        ).fetchall()}
        self.assertEqual(handles[a], "alphacoder")
        self.assertEqual(handles[b], "preexisting")
        self.assertEqual(handles[c], "charlie_qa")
        self.assertEqual(handles[d], "deltauser")

    def test_collision_skipped(self) -> None:
        a = upsert_person(self.con, full_name="A", notes="t.me/sharedhandle")
        b = upsert_person(self.con, full_name="B", notes="t.me/sharedhandle")
        stats = discover_telegram_handles(self.con)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["skipped_collision"], 1)


class SetTelegramHandleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_set_by_email(self) -> None:
        from network_chief.discovery import set_telegram_handle
        upsert_person(self.con, full_name="Alice", email="alice@example.com")
        result = set_telegram_handle(self.con, email="alice@example.com", handle="@alice_tg")
        self.assertTrue(result["matched"])
        row = self.con.execute("SELECT telegram_handle FROM people WHERE primary_email = 'alice@example.com'").fetchone()
        self.assertEqual(row[0], "alice_tg")

    def test_set_normalises_full_url(self) -> None:
        from network_chief.discovery import set_telegram_handle
        upsert_person(self.con, full_name="Bob", email="bob@example.com")
        result = set_telegram_handle(self.con, email="bob@example.com", handle="https://t.me/bob_handle")
        self.assertTrue(result["matched"])
        self.assertEqual(result["handle"], "bob_handle")

    def test_no_match(self) -> None:
        from network_chief.discovery import set_telegram_handle
        result = set_telegram_handle(self.con, email="nobody@example.com", handle="x")
        self.assertFalse(result["matched"])


class BulkImportTest(unittest.TestCase):
    def test_csv_round_trip(self) -> None:
        import csv as _csv
        import tempfile
        from pathlib import Path
        from network_chief.discovery import import_telegram_csv

        con = connect(":memory:")
        init_db(con)
        upsert_person(con, full_name="A", email="a@example.com")
        upsert_person(con, full_name="B", email="b@example.com")

        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "tg.csv"
            with csv_path.open("w", newline="") as fh:
                writer = _csv.DictWriter(fh, fieldnames=["email", "handle"])
                writer.writeheader()
                writer.writerow({"email": "a@example.com", "handle": "@aaa"})
                writer.writerow({"email": "b@example.com", "handle": "t.me/bbb"})
                writer.writerow({"email": "missing@example.com", "handle": "ccc"})
            stats = import_telegram_csv(con, str(csv_path))
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["unmatched"], 1)
        handles = sorted(
            row[0] for row in con.execute(
                "SELECT telegram_handle FROM people WHERE telegram_handle IS NOT NULL"
            ).fetchall()
        )
        self.assertEqual(handles, ["aaa", "bbb"])


if __name__ == "__main__":
    unittest.main()
