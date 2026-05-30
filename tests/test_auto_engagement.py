import unittest
from unittest import mock

from network_chief.auth.errors import AuthRequired
from network_chief.auth.tokens import TokenStore
from network_chief.db import (
    add_interaction,
    connect,
    init_db,
    new_id,
    now_iso,
    upsert_person,
)
from network_chief.importers import google_api, x_api
from network_chief.policy import AutonomyPolicy


def _seed_google_token(con, scopes=None):
    TokenStore(con).save(
        provider="google", account="me@example.com", access_token="A",
        refresh_token="R", scopes=scopes or google_api.DEFAULT_SCOPES,
        expires_at="2099-01-01T00:00:00Z", extra={"client_id": "cid"},
    )


def _seed_x_token(con, scopes=None):
    TokenStore(con).save(
        provider="x", account="me", access_token="A",
        refresh_token="R", scopes=scopes or x_api.DEFAULT_SCOPES,
        expires_at="2099-01-01T00:00:00Z", extra={"client_id": "cid", "user_id": "42"},
    )


# A daytime hour outside default quiet hours (21,8).
NOON_POLICY_GMAIL = AutonomyPolicy(level=2, dry_run=False, daily_send_cap=10,
                                   quiet_hours=(0, 0), gmail_auto_reply_enabled=True,
                                   require_prior_reply=False)
NOON_POLICY_X = AutonomyPolicy(level=2, dry_run=False, daily_send_cap=10,
                               quiet_hours=(0, 0), channels_enabled=("x",),
                               x_post_enabled=True, x_reply_enabled=True)


class GmailAutoReplyTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_google_token(self.con)
        # Known active contact with a recent inbound, no outgoing reply yet.
        self.pid = upsert_person(self.con, full_name="Alice", email="alice@example.com")
        add_interaction(self.con, person_id=self.pid, channel="gmail", direction="incoming",
                        subject="Quick question", body_summary="Have a sec?",
                        occurred_at=now_iso(), source="gmail_api", source_ref="m1")

    def test_disabled_by_default(self):
        with mock.patch.object(google_api, "request_json") as rq:
            res = google_api.auto_reply_to_gmail(self.con, policy=AutonomyPolicy(level=2, dry_run=False))
        rq.assert_not_called()
        self.assertIn("policy.gmail_auto_reply_enabled is false", res.get("skipped", ""))

    def test_drafts_and_sends_acknowledgement(self):
        with mock.patch.object(google_api, "request_json", return_value={"id": "msg-1", "threadId": "t-1"}):
            res = google_api.auto_reply_to_gmail(self.con, policy=NOON_POLICY_GMAIL)
        self.assertEqual(res["sent"], 1)
        row = self.con.execute(
            "SELECT status, rationale, subject FROM drafts WHERE channel='gmail' AND rationale='auto-ack'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "sent")
        self.assertTrue(row["subject"].startswith("Re:"))

    def test_skips_if_we_already_replied(self):
        # Add an outgoing reply strictly after the inbound — autopilot must skip.
        self.con.execute(
            "UPDATE interactions SET occurred_at = '2026-05-29T10:00:00Z' WHERE source_ref = 'm1'"
        )
        self.con.commit()
        add_interaction(self.con, person_id=self.pid, channel="gmail", direction="outgoing",
                        subject="Re: Quick question", body_summary="thanks",
                        occurred_at="2026-05-29T11:00:00Z", source="gmail_api", source_ref="m2")
        with mock.patch.object(google_api, "request_json") as rq:
            res = google_api.auto_reply_to_gmail(self.con, policy=NOON_POLICY_GMAIL)
        rq.assert_not_called()
        self.assertEqual(res["considered"], 0)

    def test_filters_bot_addresses_and_self(self):
        bots_and_self = [
            "donotreply@interactivebrokers.com",        # local 'donotreply'
            "no-reply-corporate-actions@alfabank.ru",   # local starts 'no-reply'
            "no_reply@gateway.monetico-retail.com",     # local 'no_reply'
            "noreply.enelenergia@enel.com",             # 'noreply' before '.'
            "tradingassistant@interactivebrokers.com",  # 'trading' / 'tradingassistant'
            "security@mail.instagram.com",              # bot domain prefix mail.
            "pps@notif.athle.fr",                       # bot domain prefix notif.
            "newsletter@example.com",
            "contact@helloasso.com",
            "me@example.com",                           # owner (token account)
        ]
        for email in bots_and_self:
            pid = upsert_person(self.con, full_name=email.split("@")[0], email=email)
            add_interaction(self.con, person_id=pid, channel="gmail", direction="incoming",
                            subject="Auto", body_summary="x", occurred_at=now_iso(),
                            source="gmail_api", source_ref=f"m-{email}")
        with mock.patch.object(google_api, "request_json", return_value={"id": "m", "threadId": "t"}):
            res = google_api.auto_reply_to_gmail(self.con, policy=NOON_POLICY_GMAIL)
        # Only the original Alice@example.com (a real human) should have been auto-acked.
        self.assertEqual(res["sent"], 1)
        recipients = {r["to"] for r in res["items"]}
        self.assertEqual(recipients, {"alice@example.com"})

    def test_bot_address_helper_unit(self):
        from network_chief.importers.google_api import _is_bot_address
        owners = {"me@example.com"}
        # bots
        for e in ("noreply@x.com", "no-reply@x.com", "donotreply@x.com",
                  "tradingassistant@interactivebrokers.com", "newsletter@x.com",
                  "security@mail.instagram.com", "pps@notif.athle.fr"):
            self.assertTrue(_is_bot_address(e, owners), f"should be bot: {e}")
        # humans
        for e in ("alice@example.com", "maria.konovalenko@gmail.com",
                  "p.bangert@algorithmica-technologies.com", "dmitry@dumik.com"):
            self.assertFalse(_is_bot_address(e, owners), f"should NOT be bot: {e}")
        # owner
        self.assertTrue(_is_bot_address("me@example.com", owners))

    def test_dry_run_makes_no_http_call(self):
        pol = AutonomyPolicy(level=2, dry_run=True, daily_send_cap=10,
                             quiet_hours=(0, 0), gmail_auto_reply_enabled=True,
                             require_prior_reply=False)
        with mock.patch.object(google_api, "request_json") as rq:
            res = google_api.auto_reply_to_gmail(self.con, policy=pol)
        rq.assert_not_called()
        # Drafted but not sent.
        self.assertEqual(res["drafted"], 1)
        self.assertEqual(res["sent"], 0)


class XPostTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_x_token(self.con)
        # Approved x_post draft.
        ts = now_iso()
        self.con.execute(
            "INSERT INTO drafts (id, channel, subject, body, status, created_at, updated_at) "
            "VALUES (?, 'x_post', 'X post', ?, 'approved', ?, ?)",
            (new_id(), "Strong networks are living maps of trust.", ts, ts),
        )
        self.con.commit()

    def test_disabled_by_default(self):
        with mock.patch.object(x_api, "request_json") as rq:
            res = x_api.post_x_drafts(self.con, policy=AutonomyPolicy(level=2, dry_run=False))
        rq.assert_not_called()
        self.assertEqual(res["posted"], 0)

    def test_channel_must_be_enabled(self):
        pol = AutonomyPolicy(level=2, dry_run=False, x_post_enabled=True,
                             channels_enabled=("gmail",), quiet_hours=(0, 0))
        with mock.patch.object(x_api, "request_json") as rq:
            res = x_api.post_x_drafts(self.con, policy=pol)
        rq.assert_not_called()
        self.assertIn("channels_enabled", res.get("reason", ""))

    def test_posts_when_enabled(self):
        with mock.patch.object(x_api, "request_json", return_value={"data": {"id": "1234567890"}}):
            res = x_api.post_x_drafts(self.con, policy=NOON_POLICY_X)
        self.assertEqual(res["posted"], 1)
        row = self.con.execute("SELECT status FROM drafts WHERE channel='x_post'").fetchone()
        self.assertEqual(row["status"], "sent")


class XReplyTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_x_token(self.con)
        self.pid = upsert_person(self.con, full_name="Mentioner", email=None)
        self.con.execute("UPDATE people SET twitter_handle='mentioner' WHERE id=?", (self.pid,))
        add_interaction(self.con, person_id=self.pid, channel="x", direction="incoming",
                        subject="X mention", body_summary="hey @me check this out",
                        occurred_at=now_iso(), source="x_api", source_ref="tweet-100")
        self.con.commit()

    def test_disabled_by_default(self):
        with mock.patch.object(x_api, "request_json") as rq:
            res = x_api.reply_to_x_mentions(self.con, policy=AutonomyPolicy(level=2, dry_run=False))
        rq.assert_not_called()
        self.assertEqual(res["replied"], 0)

    def test_replies_with_in_reply_to(self):
        captured = {}
        def fake(method, url, headers=None, params=None, json_body=None, **kw):
            captured.update(json_body or {})
            return {"data": {"id": "9999"}}
        with mock.patch.object(x_api, "request_json", side_effect=fake):
            res = x_api.reply_to_x_mentions(self.con, policy=NOON_POLICY_X)
        self.assertEqual(res["replied"], 1)
        self.assertEqual(captured.get("reply", {}).get("in_reply_to_tweet_id"), "tweet-100")
        # local audit draft was created and marked sent
        row = self.con.execute("SELECT status,rationale FROM drafts WHERE channel='x'").fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["rationale"], "auto-x-reply")


class LinkedInPublishAssistTest(unittest.TestCase):
    def test_marks_draft_sent_and_returns_share_url(self):
        from network_chief.engagement import publish_linkedin_assist
        con = connect(":memory:")
        init_db(con)
        ts = now_iso()
        did = new_id()
        con.execute(
            "INSERT INTO drafts (id, channel, subject, body, status, created_at, updated_at) "
            "VALUES (?, 'linkedin_post', 'X', 'Network thoughts.', 'approved', ?, ?)",
            (did, ts, ts),
        )
        con.commit()
        # Mock browser open + ensure no clipboard tool is required.
        with mock.patch("webbrowser.open"), mock.patch("shutil.which", return_value=None):
            res = publish_linkedin_assist(con, open_browser=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["draft_id"], did)
        self.assertIn("shareActive=true", res["share_url"])
        status = con.execute("SELECT status FROM drafts WHERE id=?", (did,)).fetchone()[0]
        self.assertEqual(status, "sent")


if __name__ == "__main__":
    unittest.main()
