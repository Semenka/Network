import unittest

from network_chief.db import connect, init_db, upsert_person
from network_chief.drafts import choose_channel, compose_draft
from network_chief.engagement import prepare_telegram_keepalive, render_telegram_links


class ChannelPreferenceTest(unittest.TestCase):
    def test_telegram_wins_over_gmail(self) -> None:
        self.assertEqual(
            choose_channel({"telegram_handle": "alice", "primary_email": "a@x.com"}),
            "telegram",
        )

    def test_gmail_when_no_telegram(self) -> None:
        self.assertEqual(choose_channel({"primary_email": "a@x.com"}), "gmail")


class TelegramComposerTest(unittest.TestCase):
    def test_telegram_body_is_short_and_informal(self) -> None:
        person = {"full_name": "Alice Investor", "organizations": "Acme"}
        out = compose_draft(person, channel="telegram")
        self.assertIn("Hey Alice", out["body"])
        # Telegram body should NOT have an email-style "Best,\nAndrey" closer
        self.assertNotIn("Best,\nAndrey", out["body"])
        self.assertLess(len(out["body"]), 280)


class PrepareKeepaliveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_only_picks_people_with_telegram_handle(self) -> None:
        upsert_person(self.con, full_name="No Telegram", email="no@example.com")
        upsert_person(
            self.con, full_name="Tg User", email="t@example.com", twitter_handle=None,
        )
        # Manually attach a telegram handle.
        self.con.execute(
            "UPDATE people SET telegram_handle = 'tg_user' WHERE full_name = 'Tg User'"
        )
        # Add a high-value signal so they rank.
        from network_chief.db import add_connection_value
        tg_id = self.con.execute("SELECT id FROM people WHERE full_name = 'Tg User'").fetchone()[0]
        add_connection_value(
            self.con, person_id=tg_id, value_type="competence", description="Operator", score=80,
        )
        self.con.commit()

        ids = prepare_telegram_keepalive(self.con, limit=5)
        self.assertEqual(len(ids), 1)
        # The single draft should be telegram-channelled.
        chan = self.con.execute("SELECT channel FROM drafts WHERE id = ?", (ids[0],)).fetchone()[0]
        self.assertEqual(chan, "telegram")


class TelegramLinksTest(unittest.TestCase):
    def test_links_render_with_urlencoded_body(self) -> None:
        con = connect(":memory:")
        init_db(con)
        pid = upsert_person(con, full_name="Bob Builder", email=None)
        con.execute("UPDATE people SET telegram_handle = 'bob_builder' WHERE id = ?", (pid,))
        from network_chief.db import new_id, now_iso
        ts = now_iso()
        con.execute(
            """
            INSERT INTO drafts (id, person_id, channel, subject, body, status, created_at, updated_at)
            VALUES (?, ?, 'telegram', 'Catch-up', 'Hey Bob — quick one. Free for a 15-min call?', 'draft', ?, ?)
            """,
            (new_id(), pid, ts, ts),
        )
        con.commit()
        md = render_telegram_links(con)
        self.assertIn("# Telegram drafts (1)", md)
        self.assertIn("Bob Builder — @bob_builder", md)
        self.assertIn("https://t.me/bob_builder?text=", md)
        # URL-encoded body should appear.
        self.assertIn("Hey%20Bob", md)


if __name__ == "__main__":
    unittest.main()
