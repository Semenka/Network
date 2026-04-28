import unittest

from network_chief.auth.tokens import TokenStore, expires_at_from_seconds, is_expired
from network_chief.db import connect, init_db


class TokenStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        self.store = TokenStore(self.con)

    def test_save_and_get(self) -> None:
        self.store.save(
            provider="google",
            account="alice@example.com",
            access_token="a",
            refresh_token="r",
            expires_at="2030-01-01T00:00:00Z",
            scopes="openid email",
            extra={"client_id": "cid"},
        )
        record = self.store.get("google", "alice@example.com")
        self.assertIsNotNone(record)
        self.assertEqual(record["access_token"], "a")
        self.assertEqual(record["refresh_token"], "r")
        self.assertEqual(record["extra"], {"client_id": "cid"})

    def test_unique_per_provider_account(self) -> None:
        self.store.save(provider="x", account="hank", access_token="t1", scopes="")
        self.store.save(provider="x", account="hank", access_token="t2", scopes="")
        rows = self.store.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["access_token"], "t2")

    def test_get_without_account_returns_latest(self) -> None:
        self.store.save(provider="x", account="hank", access_token="t1", scopes="")
        record = self.store.get("x")
        self.assertIsNotNone(record)
        self.assertEqual(record["account"], "hank")

    def test_delete(self) -> None:
        self.store.save(provider="x", account="hank", access_token="t1", scopes="")
        self.assertEqual(self.store.delete("x", "hank"), 1)
        self.assertIsNone(self.store.get("x"))

    def test_mark_refreshed_preserves_refresh_token_when_omitted(self) -> None:
        token_id = self.store.save(
            provider="google",
            account="alice@example.com",
            access_token="a1",
            refresh_token="r1",
            scopes="",
        )
        self.store.mark_refreshed(token_id, access_token="a2", expires_at="2030-01-01T00:00:00Z")
        record = self.store.get("google", "alice@example.com")
        self.assertEqual(record["access_token"], "a2")
        self.assertEqual(record["refresh_token"], "r1")


class ExpiryHelpersTest(unittest.TestCase):
    def test_expires_at_from_seconds(self) -> None:
        self.assertIsNone(expires_at_from_seconds(None))
        self.assertIsNone(expires_at_from_seconds(0))
        value = expires_at_from_seconds(3600)
        self.assertIsNotNone(value)
        self.assertTrue(value.endswith("Z"))

    def test_is_expired(self) -> None:
        self.assertFalse(is_expired(None))
        self.assertFalse(is_expired("garbage"))
        self.assertTrue(is_expired("2000-01-01T00:00:00Z"))
        self.assertFalse(is_expired("2099-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
