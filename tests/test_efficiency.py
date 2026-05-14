import unittest
from datetime import UTC, datetime, timedelta

from network_chief.auth.tokens import TokenStore
from network_chief.db import (
    add_connection_value,
    connect,
    create_goal,
    init_db,
    new_id,
    record_draft_event,
    upsert_person,
)
from network_chief.drafts import create_custom_draft, create_draft, set_draft_status
from network_chief.efficiency import (
    build_outcome_sweep,
    build_review_queue,
    build_source_health,
    render_outcome_sweep_markdown,
    render_review_queue_markdown,
    render_source_health_markdown,
)


def _days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FakeGBrain:
    binary = "fake-gbrain"

    def __init__(self, available: bool = False) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available


class DraftDedupeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_create_draft_reuses_open_draft_across_days(self) -> None:
        person_id = upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")
        goal_id = create_goal(self.con, title="Grow useful conversations", cadence="weekly")
        person = {"id": person_id, "full_name": "Alice Energy", "primary_email": "alice@example.com"}
        goal = {"id": goal_id, "title": "Grow useful conversations"}

        first = create_draft(self.con, person=person, goal=goal, channel="gmail")
        self.con.execute(
            "UPDATE drafts SET created_at = ?, updated_at = ? WHERE id = ?",
            (_days_ago(3), _days_ago(3), first),
        )
        self.con.commit()

        second = create_draft(self.con, person=person, goal=goal, channel="gmail")

        self.assertEqual(first, second)
        self.assertEqual(self.con.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)

    def test_create_custom_draft_reuses_identical_open_public_draft(self) -> None:
        first = create_custom_draft(self.con, channel="x_post", subject="X post: ask", body="Who is testing this?")
        self.con.execute(
            "UPDATE drafts SET created_at = ?, updated_at = ? WHERE id = ?",
            (_days_ago(2), _days_ago(2), first),
        )
        self.con.commit()

        second = create_custom_draft(self.con, channel="x_post", subject="X post: ask", body="Who is testing this?")

        self.assertEqual(first, second)
        self.assertEqual(self.con.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)


class ReviewQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_review_queue_groups_duplicate_person_channel_goal(self) -> None:
        person_id = upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")
        goal_id = create_goal(self.con, title="Maintain high-signal network", cadence="weekly")
        add_connection_value(
            self.con,
            person_id=person_id,
            value_type="specific_knowledge",
            description="Energy AI operator",
            score=90,
        )
        older = create_custom_draft(
            self.con,
            channel="gmail",
            person_id=person_id,
            goal_id=goal_id,
            subject="Quick catch-up",
            body="Older body",
        )
        self.con.execute(
            "UPDATE drafts SET created_at = ?, updated_at = ? WHERE id = ?",
            (_days_ago(2), _days_ago(2), older),
        )
        newer = create_custom_draft(
            self.con,
            channel="gmail",
            person_id=person_id,
            goal_id=goal_id,
            subject="Quick catch-up",
            body="Newer body",
        )
        self.con.commit()

        queue = build_review_queue(self.con, limit=12)
        rendered = render_review_queue_markdown(queue)

        self.assertEqual(queue["pending_total"], 2)
        self.assertEqual(queue["group_total"], 1)
        self.assertIn("(2 grouped)", rendered)
        self.assertIn(newer, rendered)
        self.assertIn("send-approved-gmail", rendered)


class SourceHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_source_health_flags_missing_tokens_and_channels(self) -> None:
        upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")

        health = build_source_health(self.con, top_n=1, adapter=FakeGBrain(False))
        rendered = render_source_health_markdown(health)

        names = {connector["name"]: connector for connector in health["connectors"]}
        self.assertFalse(names["Google People"]["ok"])
        self.assertFalse(names["LinkedIn posting"]["ok"])
        self.assertFalse(names["X API"]["ok"])
        self.assertIn("NEEDS SETUP", rendered)
        self.assertIn("auth-google", rendered)

    def test_source_health_accepts_valid_google_token(self) -> None:
        TokenStore(self.con).save(
            provider="google",
            account="you@example.com",
            access_token="token",
            scopes=(
                "https://www.googleapis.com/auth/contacts.readonly "
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/gmail.compose"
            ),
        )

        health = build_source_health(self.con, top_n=1, adapter=FakeGBrain(True))
        names = {connector["name"]: connector for connector in health["connectors"]}

        self.assertTrue(names["Google People"]["ok"])
        self.assertTrue(names["Gmail API"]["ok"])
        self.assertTrue(names["Gmail Drafts"]["ok"])


class OutcomeSweepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_outcome_sweep_finds_delivery_and_metric_gaps(self) -> None:
        person_id = upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")
        gmail = create_custom_draft(self.con, channel="gmail", person_id=person_id, subject="Hi", body="Body")
        set_draft_status(self.con, gmail, "approved")
        post = create_custom_draft(self.con, channel="linkedin_post", subject="Post", body="Public body")
        record_draft_event(self.con, draft_id=post, event_type="published", external_ref="linkedin:1")

        sweep = build_outcome_sweep(self.con, since_days=7)
        rendered = render_outcome_sweep_markdown(sweep)

        self.assertEqual(len(sweep["approved_without_delivery"]), 1)
        self.assertEqual(len(sweep["delivered_without_outcome"]), 1)
        self.assertEqual(len(sweep["linkedin_metrics_needed"]), 1)
        self.assertIn("record-engagement-outcome", rendered)
        self.assertIn("record-audience-metric --channel linkedin --metric-type impressions", rendered)


if __name__ == "__main__":
    unittest.main()
