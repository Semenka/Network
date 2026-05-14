import unittest
from unittest import mock

from network_chief.auth.errors import RateLimited
from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db
from network_chief.importers import x_api


def _seed_token(con) -> None:
    TokenStore(con).save(
        provider="x",
        account="me",
        access_token="A",
        refresh_token="R",
        scopes=x_api.DEFAULT_SCOPES,
        expires_at="2099-01-01T00:00:00Z",
        extra={"client_id": "cid", "user_id": "999"},
    )


class XFollowingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_token(self.con)

    def test_paginates_and_inserts(self) -> None:
        page1 = {
            "data": [
                {"id": "1", "username": "AliceAI", "name": "Alice", "description": "machine learning researcher"},
            ],
            "meta": {"next_token": "p2"},
        }
        page2 = {
            "data": [
                {"id": "2", "username": "BuilderBob", "name": "Bob", "description": "founder & operator"},
            ],
            "meta": {},
        }
        responses = [page1, page2]

        def fake_call(method, url, headers=None, params=None, **kwargs):
            return responses.pop(0)

        with mock.patch("network_chief.importers.x_api.request_json", side_effect=fake_call):
            stats = x_api.sync_x_following(self.con)
        self.assertEqual(stats["people_seen"], 2)
        self.assertEqual(stats["status"], "ok")
        handles = sorted(row["twitter_handle"] for row in self.con.execute("SELECT twitter_handle FROM people"))
        self.assertEqual(handles, ["aliceai", "builderbob"])

    def test_rate_limited_returns_partial(self) -> None:
        def fake_call(method, url, headers=None, params=None, **kwargs):
            raise RateLimited("rate", reset_at="1700000000")

        with mock.patch("network_chief.importers.x_api.request_json", side_effect=fake_call):
            stats = x_api.sync_x_following(self.con)
        self.assertEqual(stats["status"], "rate_limited")
        self.assertEqual(stats["people_seen"], 0)
        self.assertEqual(stats["reset_at"], "1700000000")


class XMentionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_token(self.con)

    def test_mention_creates_interaction(self) -> None:
        response = {
            "data": [
                {
                    "id": "t1",
                    "author_id": "42",
                    "text": "hey @me check this",
                    "created_at": "2026-04-27T10:00:00Z",
                }
            ],
            "includes": {
                "users": [
                    {"id": "42", "username": "FollowerFran", "name": "Fran", "description": "investor"}
                ]
            },
        }

        def fake_call(method, url, headers=None, params=None, **kwargs):
            return response

        with mock.patch("network_chief.importers.x_api.request_json", side_effect=fake_call):
            stats = x_api.sync_x_mentions(self.con, max_pages=1)
        self.assertEqual(stats["mentions_seen"], 1)
        interaction = self.con.execute("SELECT direction, channel, source_ref FROM interactions").fetchone()
        self.assertEqual(interaction["direction"], "incoming")
        self.assertEqual(interaction["channel"], "x")
        self.assertEqual(interaction["source_ref"], "t1")


if __name__ == "__main__":
    unittest.main()
