import unittest
from unittest import mock

from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db
from network_chief.importers import google_api


class FakeQueue:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[tuple] = []

    def __call__(self, method, url, headers=None, params=None, **kwargs):
        self.calls.append((method, url, dict(params or {})))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def _seed_token(con, *, account: str = "alice@example.com") -> None:
    TokenStore(con).save(
        provider="google",
        account=account,
        access_token="ACCESS",
        refresh_token="REFRESH",
        scopes=google_api.DEFAULT_SCOPES,
        expires_at="2099-01-01T00:00:00Z",
        extra={"client_id": "cid"},
    )


class GoogleContactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_token(self.con)

    def test_sync_paginates_and_inserts(self) -> None:
        page1 = {
            "connections": [
                {
                    "resourceName": "people/c1",
                    "names": [{"displayName": "Alice Investor", "metadata": {"primary": True}}],
                    "emailAddresses": [{"value": "alice@vc.example", "metadata": {"primary": True}}],
                    "organizations": [{"name": "Acme Ventures", "title": "Partner"}],
                    "biographies": [{"value": "Angel investor focused on AI"}],
                }
            ],
            "nextPageToken": "p2",
        }
        page2 = {
            "connections": [
                {
                    "resourceName": "people/c2",
                    "names": [{"displayName": "Bob Builder"}],
                    "emailAddresses": [{"value": "bob@example.com"}],
                    "biographies": [{"value": "Founder & engineer"}],
                }
            ]
        }
        fake = FakeQueue([page1, page2])
        with mock.patch("network_chief.importers.google_api.request_json", side_effect=fake):
            stats = google_api.sync_google_contacts(self.con)
        self.assertEqual(stats["people_seen"], 2)
        self.assertEqual(stats["pages"], 2)
        people = self.con.execute("SELECT primary_email FROM people ORDER BY primary_email").fetchall()
        self.assertEqual([row["primary_email"] for row in people], ["alice@vc.example", "bob@example.com"])
        # Acme Ventures org + role inserted.
        org = self.con.execute("SELECT name FROM organizations").fetchone()
        self.assertEqual(org["name"], "Acme Ventures")
        # Inferred connection_value from "investor" / "founder" keywords.
        values = self.con.execute("SELECT value_type FROM connection_values").fetchall()
        types = {row["value_type"] for row in values}
        self.assertIn("financial_capital", types)
        # Pagination uses pageToken=p2 on second request.
        self.assertIn("pageToken", fake.calls[1][2])


class GmailMessagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_token(self.con, account="me@example.com")

    def test_sync_messages_dedupes_on_repeat(self) -> None:
        list_response = {"messages": [{"id": "m1"}]}
        message = {
            "id": "m1",
            "snippet": "Quick intro to a fund",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Carol VC <carol@fund.example>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Intro"},
                    {"name": "Date", "value": "Mon, 7 Apr 2026 10:00:00 +0000"},
                ]
            },
        }
        fake1 = FakeQueue([list_response, message])
        with mock.patch("network_chief.importers.google_api.request_json", side_effect=fake1):
            stats = google_api.sync_gmail_messages(self.con)
        self.assertEqual(stats["messages_seen"], 1)
        self.assertEqual(stats["people_seen"], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM interactions").fetchone()[0], 1)

        fake2 = FakeQueue([list_response, message])
        with mock.patch("network_chief.importers.google_api.request_json", side_effect=fake2):
            stats2 = google_api.sync_gmail_messages(self.con)
        # Same message id → add_interaction must dedupe.
        self.assertEqual(stats2["messages_seen"], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM interactions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
