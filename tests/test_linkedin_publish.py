import inspect
import unittest

from network_chief.auth.errors import AuthRequired
from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db
from network_chief.drafts import create_custom_draft, set_draft_status
from network_chief import linkedin_publish
from network_chief.linkedin_publish import LinkedInPublishError, publish_approved_linkedin


class LinkedInPublishTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        self.draft_id = create_custom_draft(
            self.con,
            channel="linkedin_post",
            subject="AI in energy",
            body="AI in energy is becoming an operating discipline.",
            rationale="Daily post",
        )

    def test_blocks_without_token_or_scope(self) -> None:
        set_draft_status(self.con, self.draft_id, "approved")
        with self.assertRaises(AuthRequired):
            publish_approved_linkedin(
                self.con,
                draft_id=self.draft_id,
                confirm_exact_text="AI in energy is becoming an operating discipline.",
            )

        TokenStore(self.con).save(
            provider="linkedin",
            account="owner",
            access_token="token",
            scopes="openid profile email",
            extra={"sub": "person123"},
        )
        with self.assertRaises(AuthRequired):
            publish_approved_linkedin(
                self.con,
                draft_id=self.draft_id,
                confirm_exact_text="AI in energy is becoming an operating discipline.",
            )

    def test_exact_confirmation_and_mocked_official_publish(self) -> None:
        set_draft_status(self.con, self.draft_id, "approved")
        TokenStore(self.con).save(
            provider="linkedin",
            account="owner",
            access_token="token",
            scopes="openid profile email w_member_social",
            extra={"sub": "person123"},
        )

        with self.assertRaises(LinkedInPublishError):
            publish_approved_linkedin(self.con, draft_id=self.draft_id, confirm_exact_text="Different text")

        captured = {}

        def request_fn(method, url, *, headers, json_body):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json_body
            return {"id": "urn:li:share:123"}

        result = publish_approved_linkedin(
            self.con,
            draft_id=self.draft_id,
            confirm_exact_text="AI in energy is becoming an operating discipline.",
            request_fn=request_fn,
        )

        self.assertEqual(result["status"], "published")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["payload"]["author"], "urn:li:person:person123")
        draft = self.con.execute("SELECT status FROM drafts WHERE id = ?", (self.draft_id,)).fetchone()
        event = self.con.execute(
            "SELECT event_type, external_ref FROM draft_events WHERE draft_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.draft_id,),
        ).fetchone()
        self.assertEqual(draft["status"], "published")
        self.assertEqual(event["event_type"], "published")
        self.assertEqual(event["external_ref"], "urn:li:share:123")

    def test_linkedin_publish_module_has_no_browser_automation_path(self) -> None:
        source = inspect.getsource(linkedin_publish).lower()
        for forbidden in ("selenium", "playwright", "browser cookie", "linkedin password"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
