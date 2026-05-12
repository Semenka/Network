import unittest

from network_chief.db import connect, init_db, upsert_person
from network_chief.drafts import create_draft, record_engagement_outcome


class EngagementOutcomeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_records_outcome_event_status_and_metric(self) -> None:
        person_id = upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")
        draft_id = create_draft(
            self.con,
            person={"id": person_id, "full_name": "Alice Energy", "primary_email": "alice@example.com"},
            channel="gmail",
        )

        result = record_engagement_outcome(
            self.con,
            draft_id=draft_id,
            outcome="meeting",
            note="Booked a useful call",
            external_ref="calendar:123",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "converted")
        draft = self.con.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        event = self.con.execute("SELECT event_type, reason_code FROM draft_events WHERE id = ?", (result["event_id"],)).fetchone()
        metric = self.con.execute("SELECT channel, metric_type, external_ref FROM audience_metrics WHERE id = ?", (result["metric_id"],)).fetchone()
        self.assertEqual(draft["status"], "converted")
        self.assertEqual(event["event_type"], "converted")
        self.assertEqual(event["reason_code"], "meeting")
        self.assertEqual(metric["channel"], "gmail")
        self.assertEqual(metric["metric_type"], "meetings")
        self.assertEqual(metric["external_ref"], "calendar:123")

    def test_unknown_draft_returns_none(self) -> None:
        self.assertIsNone(record_engagement_outcome(self.con, draft_id="missing", outcome="reply"))


if __name__ == "__main__":
    unittest.main()
