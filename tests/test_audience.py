import unittest

from network_chief.audience import build_scorecard, prepare_audience_brief
from network_chief.db import connect, create_goal, init_db, record_audience_metric, upsert_person
from network_chief.drafts import apply_draft_event, create_custom_draft, set_draft_status


class AudienceGrowthTest(unittest.TestCase):
    def test_schema_upgrades_are_idempotent(self):
        con = connect(":memory:")
        init_db(con)
        init_db(con)

        tables = {
            row["name"]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('draft_events', 'audience_metrics')"
            )
        }
        self.assertEqual(tables, {"draft_events", "audience_metrics"})

    def test_draft_events_track_approve_reject_and_outcomes(self):
        con = connect(":memory:")
        init_db(con)
        draft_id = create_custom_draft(
            con,
            channel="x_post",
            subject="X post",
            body="Testing a public network prompt.",
            rationale="Audience growth",
        )

        self.assertTrue(set_draft_status(con, draft_id, "approved", reason_code="good_timing", note="Ship it"))
        event_id = apply_draft_event(con, draft_id=draft_id, event_type="published", external_ref="x:123")

        self.assertIsNotNone(event_id)
        draft = con.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        events = con.execute("SELECT event_type, reason_code FROM draft_events").fetchall()
        self.assertEqual(draft["status"], "published")
        self.assertEqual({event["event_type"] for event in events}, {"approve", "published"})
        approve = con.execute("SELECT reason_code FROM draft_events WHERE event_type = 'approve'").fetchone()
        self.assertEqual(approve["reason_code"], "good_timing")

    def test_audience_brief_creates_public_and_followup_drafts(self):
        con = connect(":memory:")
        init_db(con)
        upsert_person(
            con,
            full_name="Alice AI",
            email="alice@example.com",
            linkedin_url="https://linkedin.com/in/alice",
            twitter_handle="@aliceai",
            notes="AI operator and machine learning expert.",
        )
        create_goal(
            con,
            title="Grow AI operator audience",
            cadence="weekly",
            capital_type="competence",
            target_segment="AI operators",
            success_metric="3 useful public conversations",
        )

        brief = prepare_audience_brief(
            con,
            topic="AI operator audience",
            linkedin_posts=1,
            x_posts=1,
            x_comments=1,
            gmail_followups=1,
        )

        channels = {row["channel"] for row in con.execute("SELECT channel FROM drafts")}
        self.assertIn("Network Chief Audience Brief", brief)
        self.assertIn("Drafts Prepared", brief)
        self.assertTrue({"linkedin_post", "x_post", "x_comment", "gmail"}.issubset(channels))

    def test_scorecard_aggregates_events_and_metrics(self):
        con = connect(":memory:")
        init_db(con)
        draft_id = create_custom_draft(con, channel="linkedin_post", subject="Post", body="Body")
        apply_draft_event(con, draft_id=draft_id, event_type="approve")
        apply_draft_event(con, draft_id=draft_id, event_type="published")
        apply_draft_event(con, draft_id=draft_id, event_type="response", note="Useful reply")
        record_audience_metric(con, channel="linkedin", metric_type="replies", value=2, draft_id=draft_id)

        scorecard = build_scorecard(con, days=7)

        self.assertIn("Draft approvals: 1", scorecard)
        self.assertIn("linkedin / replies: 2", scorecard)
        self.assertIn("response: 1", scorecard)


if __name__ == "__main__":
    unittest.main()
