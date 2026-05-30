import base64
import unittest
from unittest import mock

from network_chief.auth.errors import AuthRequired
from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db, new_id, now_iso, upsert_person
from network_chief.importers import google_api


def _seed_token(con, *, with_compose: bool = True) -> None:
    scopes = google_api.DEFAULT_SCOPES if with_compose else (
        "openid email https://www.googleapis.com/auth/gmail.readonly"
    )
    TokenStore(con).save(
        provider="google",
        account="me@example.com",
        access_token="ACCESS",
        refresh_token="REFRESH",
        scopes=scopes,
        expires_at="2099-01-01T00:00:00Z",
        extra={"client_id": "cid"},
    )


def _seed_drafts(con, n: int = 2) -> list[str]:
    ids = []
    for i in range(n):
        person_id = upsert_person(con, full_name=f"Person {i}", email=f"p{i}@example.com")
        draft_id = new_id()
        ts = now_iso()
        con.execute(
            """
            INSERT INTO drafts (id, person_id, channel, subject, body, status, created_at, updated_at)
            VALUES (?, ?, 'gmail', ?, ?, 'draft', ?, ?)
            """,
            (draft_id, person_id, "Quick catch-up", f"Hi Person {i},\\n\\nLet's reconnect.\\n\\nBest", ts, ts),
        )
        ids.append(draft_id)
    con.commit()
    return ids


class GmailPushTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_missing_compose_scope_raises(self) -> None:
        _seed_token(self.con, with_compose=False)
        _seed_drafts(self.con, n=1)
        with self.assertRaises(AuthRequired):
            google_api.push_drafts_to_gmail(self.con)

    def test_pushes_each_draft_with_base64url_mime(self) -> None:
        _seed_token(self.con, with_compose=True)
        _seed_drafts(self.con, n=2)

        seen: list[dict] = []

        def fake_request_json(method, url, headers=None, params=None, json_body=None, data=None, **kwargs):
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/users/me/drafts"))
            self.assertIn("raw", json_body["message"])
            raw = json_body["message"]["raw"]
            # base64url decode (with padding tolerated)
            padded = raw + "=" * (-len(raw) % 4)
            mime = base64.urlsafe_b64decode(padded).decode("utf-8")
            seen.append({"raw_mime": mime})
            return {"id": f"draft_{len(seen)}", "message": {"id": f"msg_{len(seen)}"}}

        with mock.patch("network_chief.importers.google_api.request_json", side_effect=fake_request_json):
            stats = google_api.push_drafts_to_gmail(self.con)
        self.assertEqual(stats["pushed"], 2)
        self.assertEqual(stats["skipped"], 0)
        # Both pushed messages should be valid MIME with To/Subject/Date headers.
        for entry in seen:
            self.assertIn("To: p", entry["raw_mime"])
            self.assertIn("Subject: Quick catch-up", entry["raw_mime"])
            self.assertIn("From: me@example.com", entry["raw_mime"])

    def test_skips_drafts_without_email(self) -> None:
        _seed_token(self.con, with_compose=True)
        # Person with no email — should be filtered out by SQL.
        person_id = upsert_person(self.con, full_name="No Email")
        ts = now_iso()
        self.con.execute(
            """
            INSERT INTO drafts (id, person_id, channel, body, status, created_at, updated_at)
            VALUES (?, ?, 'gmail', 'hi', 'draft', ?, ?)
            """,
            (new_id(), person_id, ts, ts),
        )
        self.con.commit()
        with mock.patch("network_chief.importers.google_api.request_json") as called:
            stats = google_api.push_drafts_to_gmail(self.con)
        called.assert_not_called()
        self.assertEqual(stats["pushed"], 0)


if __name__ == "__main__":
    unittest.main()
