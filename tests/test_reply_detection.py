import unittest

from network_chief.db import (
    connect,
    init_db,
    mark_draft_pushed,
    mark_draft_responded,
    new_id,
    now_iso,
    upsert_person,
)
from network_chief.importers.google_api import _ingest_gmail_message, detect_replies_heuristic
from network_chief.scoring import rank_people


def _seed_draft(con, person_id, *, thread_id=None, sent_at=None, outcome="pending"):
    draft_id = new_id()
    ts = now_iso()
    con.execute(
        """
        INSERT INTO drafts (id, person_id, channel, body, status, outcome,
                            gmail_thread_id, sent_at, created_at, updated_at)
        VALUES (?, ?, 'gmail', 'hi', 'approved', ?, ?, ?, ?, ?)
        """,
        (draft_id, person_id, outcome, thread_id, sent_at, ts, ts),
    )
    con.commit()
    return draft_id


def _gmail_msg(*, msg_id, thread_id, from_addr, to_addr, date):
    return {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": "thanks, sounds good",
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "To", "value": to_addr},
                {"name": "Subject", "value": "Re: Quick catch-up"},
                {"name": "Date", "value": date},
            ]
        },
    }


class ReplyDetectionTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)
        self.owner = "me@example.com"
        self.pid = upsert_person(self.con, full_name="Alice", email="alice@vc.example")

    def test_incoming_on_matched_thread_marks_responded(self):
        _seed_draft(self.con, self.pid, thread_id="T1", sent_at="2026-05-01T10:00:00Z")
        msg = _gmail_msg(
            msg_id="m1", thread_id="T1",
            from_addr="Alice <alice@vc.example>", to_addr=self.owner,
            date="Mon, 5 May 2026 09:00:00 +0000",
        )
        people, interactions, replies = _ingest_gmail_message(self.con, msg, owner=self.owner)
        self.assertEqual(replies, 1)
        row = self.con.execute("SELECT outcome, responded_at FROM drafts WHERE gmail_thread_id='T1'").fetchone()
        self.assertEqual(row["outcome"], "responded")
        self.assertIsNotNone(row["responded_at"])
        # interaction tagged as a reply
        sent = self.con.execute("SELECT sentiment FROM interactions WHERE person_id=?", (self.pid,)).fetchone()
        self.assertEqual(sent["sentiment"], "reply")

    def test_unrelated_thread_ignored(self):
        _seed_draft(self.con, self.pid, thread_id="T1", sent_at="2026-05-01T10:00:00Z")
        msg = _gmail_msg(
            msg_id="m2", thread_id="OTHER",
            from_addr="Alice <alice@vc.example>", to_addr=self.owner,
            date="Mon, 5 May 2026 09:00:00 +0000",
        )
        _, _, replies = _ingest_gmail_message(self.con, msg, owner=self.owner)
        self.assertEqual(replies, 0)
        row = self.con.execute("SELECT outcome FROM drafts WHERE gmail_thread_id='T1'").fetchone()
        self.assertEqual(row["outcome"], "pending")

    def test_owner_message_on_thread_does_not_flip(self):
        _seed_draft(self.con, self.pid, thread_id="T1", sent_at="2026-05-01T10:00:00Z")
        # Message FROM the owner (outgoing) on the same thread — not a reply.
        msg = _gmail_msg(
            msg_id="m3", thread_id="T1",
            from_addr=f"Me <{self.owner}>", to_addr="alice@vc.example",
            date="Mon, 5 May 2026 09:00:00 +0000",
        )
        _, _, replies = _ingest_gmail_message(self.con, msg, owner=self.owner)
        self.assertEqual(replies, 0)
        row = self.con.execute("SELECT outcome FROM drafts WHERE gmail_thread_id='T1'").fetchone()
        self.assertEqual(row["outcome"], "pending")

    def test_reply_before_sent_ignored(self):
        _seed_draft(self.con, self.pid, thread_id="T1", sent_at="2026-05-10T10:00:00Z")
        msg = _gmail_msg(
            msg_id="m4", thread_id="T1",
            from_addr="Alice <alice@vc.example>", to_addr=self.owner,
            date="Mon, 5 May 2026 09:00:00 +0000",  # before sent_at
        )
        _, _, replies = _ingest_gmail_message(self.con, msg, owner=self.owner)
        self.assertEqual(replies, 0)

    def test_responder_bonus_ranks_higher(self):
        other = upsert_person(self.con, full_name="Bob", email="bob@example.com")
        # Mark Alice as having responded; Bob has not.
        d = _seed_draft(self.con, self.pid, thread_id="T1", sent_at="2026-05-01T10:00:00Z")
        mark_draft_responded(self.con, d, responded_at="2026-05-05T10:00:00Z")
        ranked = {p["id"]: p for p in rank_people(self.con, limit=10)}
        self.assertGreater(ranked[self.pid]["score"], ranked[other]["score"])
        self.assertIn("replied to recent outreach", ranked[self.pid]["rationale"])


class HeuristicReplyTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)

    def test_heuristic_marks_responded_on_later_incoming(self):
        from network_chief.db import add_interaction
        pid = upsert_person(self.con, full_name="Carol", email="carol@example.com")
        # Draft with NO thread id, sent_at in the past.
        did = new_id()
        ts = now_iso()
        self.con.execute(
            """
            INSERT INTO drafts (id, person_id, channel, body, status, outcome, sent_at, created_at, updated_at)
            VALUES (?, ?, 'gmail', 'hi', 'approved', 'pending', '2026-05-01T10:00:00Z', ?, ?)
            """,
            (did, pid, ts, ts),
        )
        self.con.commit()
        add_interaction(
            self.con, person_id=pid, channel="gmail", direction="incoming",
            occurred_at="2026-05-05T12:00:00Z", source="gmail_api", source_ref="x1",
        )
        out = detect_replies_heuristic(self.con)
        self.assertEqual(out["heuristic_replies_detected"], 1)
        row = self.con.execute("SELECT outcome FROM drafts WHERE id=?", (did,)).fetchone()
        self.assertEqual(row["outcome"], "responded")


if __name__ == "__main__":
    unittest.main()
