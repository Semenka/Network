import json
import tempfile
import unittest
from pathlib import Path

from network_chief.channels import add_or_update_channel_account, prepare_channel_drafts
from network_chief.db import connect, init_db, list_channel_accounts, upsert_person
from network_chief.drafts import create_custom_draft, set_draft_status
from network_chief.gmail_sync import sync_gmail
from network_chief.outbound import OutboundSafetyError, prepare_gmail_send_payload
from network_chief.voice import rebuild_voice_profile


class ChannelsVoiceGmailTest(unittest.TestCase):
    def test_schema_has_channel_accounts_and_voice_tables(self):
        con = connect(":memory:")
        init_db(con)
        init_db(con)

        tables = {
            row["name"]
            for row in con.execute(
                """
                SELECT name FROM sqlite_master
                 WHERE type = 'table'
                   AND name IN ('channel_accounts', 'voice_examples', 'voice_profile')
                """
            )
        }
        self.assertEqual(tables, {"channel_accounts", "voice_examples", "voice_profile"})

    def test_sync_gmail_imports_recent_non_promotional_messages_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gmail.json"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "id": "m1",
                                "threadId": "t1",
                                "from": "Andrey <you@example.com>",
                                "to": "Alice Builder <alice@example.com>",
                                "subject": "Quick note",
                                "snippet": "Happy to compare notes on the AI operator map.",
                                "date": "2026-05-01T10:00:00Z",
                                "labelIds": ["SENT"],
                            },
                            {
                                "id": "m2",
                                "threadId": "t2",
                                "from": "Promo <promo@example.com>",
                                "to": "you@example.com",
                                "subject": "Sale",
                                "snippet": "Promotion",
                                "date": "2026-05-01T11:00:00Z",
                                "labelIds": ["CATEGORY_PROMOTIONS"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            con = connect(":memory:")
            init_db(con)

            sync_gmail(con, file=path, mailbox_owner="you@example.com", since_months=24, max_threads=2000)
            sync_gmail(con, file=path, mailbox_owner="you@example.com", since_months=24, max_threads=2000)

            self.assertEqual(con.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM interactions").fetchone()[0], 1)
            account = list_channel_accounts(con, channel="gmail")[0]
            self.assertEqual(account["account_ref"], "alice@example.com")
            self.assertEqual(account["send_enabled"], 1)

    def test_telegram_channel_drafts_require_explicit_send_enabled_account(self):
        con = connect(":memory:")
        init_db(con)
        person_id = upsert_person(con, full_name="Alice Builder", email="alice@example.com")

        empty = prepare_channel_drafts(con, channels=["telegram"], limit=3)
        self.assertEqual(empty["telegram"], [])

        add_or_update_channel_account(con, person_id=person_id, channel="telegram", account_ref="@alice", send_enabled=True)
        prepared = prepare_channel_drafts(con, channels=["telegram"], limit=3)

        self.assertEqual(len(prepared["telegram"]), 1)
        draft = con.execute("SELECT channel FROM drafts WHERE id = ?", (prepared["telegram"][0],)).fetchone()
        self.assertEqual(draft["channel"], "telegram")

    def test_voice_profile_rebuild_uses_sent_mail_and_approved_drafts(self):
        con = connect(":memory:")
        init_db(con)
        person_id = upsert_person(con, full_name="Alice Builder", email="alice@example.com")
        con.execute(
            """
            INSERT INTO interactions (
                id, person_id, channel, direction, subject, body_summary,
                occurred_at, source, source_ref, sentiment, created_at
            )
            VALUES ('i1', ?, 'gmail', 'outgoing', 'Quick note',
                    'Happy to compare notes and share what I am seeing.',
                    '2026-05-01T10:00:00Z', 'test', 'm1', NULL, '2026-05-01T10:00:00Z')
            """,
            (person_id,),
        )
        draft_id = create_custom_draft(con, channel="gmail", person_id=person_id, subject="Hi", body="Hi Alice,\n\nHappy to help.")
        set_draft_status(con, draft_id, "approved")

        profile = rebuild_voice_profile(con, sources=["sent_mail", "approved_edits"])

        self.assertGreaterEqual(profile["examples_count"], 2)
        self.assertIn("concise", profile["summary"].lower())
        self.assertEqual(con.execute("SELECT count(*) FROM voice_examples").fetchone()[0], profile["examples_count"])

    def test_gmail_send_requires_approval_and_exact_text(self):
        con = connect(":memory:")
        init_db(con)
        person_id = upsert_person(con, full_name="Alice Builder", email="alice@example.com")
        draft_id = create_custom_draft(con, channel="gmail", person_id=person_id, subject="Hi", body="Exact body")

        with self.assertRaises(OutboundSafetyError):
            prepare_gmail_send_payload(con, draft_id=draft_id, confirm_exact_text="Exact body")

        set_draft_status(con, draft_id, "approved")
        with self.assertRaises(OutboundSafetyError):
            prepare_gmail_send_payload(con, draft_id=draft_id, confirm_exact_text="Wrong body")

        payload = prepare_gmail_send_payload(con, draft_id=draft_id, confirm_exact_text="Exact body")
        self.assertEqual(payload["to"], "alice@example.com")


if __name__ == "__main__":
    unittest.main()

