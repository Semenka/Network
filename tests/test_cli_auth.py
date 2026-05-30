import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from network_chief import cli
from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db


class CliAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        self._patch = mock.patch("network_chief.cli._connection", return_value=self.con)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_auth_status_empty(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["auth-status"])
        self.assertEqual(rc, 0)
        self.assertIn("no oauth tokens", buf.getvalue())

    def test_auth_status_lists_token(self) -> None:
        TokenStore(self.con).save(
            provider="google",
            account="alice@example.com",
            access_token="A",
            refresh_token="R",
            scopes="openid email",
            expires_at="2099-01-01T00:00:00Z",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["auth-status"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("google", out)
        self.assertIn("alice@example.com", out)
        self.assertIn("refreshable=yes", out)
        # access_token MUST NOT be printed.
        self.assertNotIn("\nA\n", out)

    def test_auth_revoke_deletes_token(self) -> None:
        TokenStore(self.con).save(
            provider="x", account="me", access_token="A", scopes="", expires_at=None
        )
        buf = io.StringIO()
        with mock.patch("network_chief.cli.revoke_x", side_effect=lambda con, account=None: TokenStore(con).delete("x", account)):
            with redirect_stdout(buf):
                rc = cli.main(["auth-revoke", "--provider", "x"])
        self.assertEqual(rc, 0)
        self.assertIn("removed 1", buf.getvalue())
        self.assertEqual(TokenStore(self.con).list(), [])


if __name__ == "__main__":
    unittest.main()
