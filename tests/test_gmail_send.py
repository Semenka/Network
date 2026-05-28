import unittest
from unittest import mock

from network_chief.auth.errors import AuthRequired
from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db, new_id, now_iso, upsert_person
from network_chief.importers import google_api
from network_chief.policy import AutonomyPolicy


def _seed_token(con, *, scopes):
    TokenStore(con).save(
        provider="google", account="me@example.com", access_token="A",
        refresh_token="R", scopes=scopes, expires_at="2099-01-01T00:00:00Z",
        extra={"client_id": "cid"},
    )


def _approved_draft(con, *, email="warm@example.com"):
    pid = upsert_person(con, full_name="Warm Contact", email=email)
    # prior reply so L1 would permit
    con.execute(
        "INSERT INTO interactions (id, person_id, channel, direction, occurred_at, source, source_ref, created_at) "
        "VALUES (?, ?, 'gmail', 'incoming', ?, 's', 'r1', ?)",
        (new_id(), pid, now_iso(), now_iso()),
    )
    did = new_id()
    ts = now_iso()
    con.execute(
        "INSERT INTO drafts (id, person_id, channel, subject, body, status, created_at, updated_at) "
        "VALUES (?, ?, 'gmail', 'Hi', 'hello', 'approved', ?, ?)",
        (did, pid, ts, ts),
    )
    con.commit()
    return did


class GmailSendTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)

    def test_missing_send_scope_raises(self):
        _seed_token(self.con, scopes="openid email https://www.googleapis.com/auth/gmail.readonly")
        _approved_draft(self.con)
        with self.assertRaises(AuthRequired):
            google_api.send_drafts_via_gmail(self.con, policy=AutonomyPolicy(level=2, dry_run=False))

    def test_dry_run_makes_no_http_call(self):
        _seed_token(self.con, scopes=google_api.DEFAULT_SCOPES)
        _approved_draft(self.con)
        with mock.patch.object(google_api, "request_json") as rq:
            res = google_api.send_drafts_via_gmail(
                self.con, policy=AutonomyPolicy(level=2, dry_run=True, quiet_hours=(0, 0)))
        rq.assert_not_called()
        self.assertEqual(res["sent"], 0)
        self.assertEqual(res["dry_run"], 1)

    def test_level0_blocks_send(self):
        _seed_token(self.con, scopes=google_api.DEFAULT_SCOPES)
        _approved_draft(self.con)
        with mock.patch.object(google_api, "request_json") as rq:
            res = google_api.send_drafts_via_gmail(
                self.con, policy=AutonomyPolicy(level=0, quiet_hours=(0, 0)))
        rq.assert_not_called()
        self.assertEqual(res["blocked"], 1)

    def test_real_send_marks_draft_sent(self):
        _seed_token(self.con, scopes=google_api.DEFAULT_SCOPES)
        did = _approved_draft(self.con)
        with mock.patch.object(google_api, "request_json", return_value={"id": "m1", "threadId": "t1"}):
            res = google_api.send_drafts_via_gmail(
                self.con, policy=AutonomyPolicy(level=2, dry_run=False, quiet_hours=(0, 0), daily_send_cap=5))
        self.assertEqual(res["sent"], 1)
        row = self.con.execute("SELECT status, sent_at FROM drafts WHERE id = ?", (did,)).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertIsNotNone(row["sent_at"])


if __name__ == "__main__":
    unittest.main()
