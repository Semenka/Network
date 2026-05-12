import json
import unittest
from datetime import UTC, datetime, timedelta

from network_chief.db import (
    add_connection_value,
    connect,
    create_goal,
    init_db,
    new_id,
    now_iso,
    record_source_run,
    upsert_person,
)
from network_chief.drafts import apply_draft_event, create_custom_draft
from network_chief.review import (
    SEV_ATTENTION,
    SEV_CRITICAL,
    SEV_OK,
    compute_review,
    previous_review,
    render_review_markdown,
    save_review,
)


def _hours_ago(h: float) -> str:
    target = datetime.now(UTC) - timedelta(hours=h)
    return target.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seed_draft(con, person_id: str, *, status: str = "draft", subject: str = "Quick catch-up", created_h_ago: float = 0.0) -> str:
    draft_id = new_id()
    created_at = _hours_ago(created_h_ago)
    con.execute(
        """
        INSERT INTO drafts (id, person_id, channel, subject, body, status, created_at, updated_at)
        VALUES (?, ?, 'gmail', ?, 'body', ?, ?, ?)
        """,
        (draft_id, person_id, subject, status, created_at, created_at),
    )
    con.commit()
    return draft_id


def _seed_kpi_snapshot(con, *, hours_ago: float, **metrics) -> None:
    payload = {
        "captured_at": _hours_ago(hours_ago),
        "window_days": 30,
        "breadth": {"total_people": metrics.get("total_people", 100)},
        "cadence": {
            "pct_active_30d": metrics.get("pct_active_30d", 10.0),
            "stale_high_value": metrics.get("stale_high_value", 50),
        },
        "pipeline": {"approval_rate_pct": metrics.get("approval_rate_pct", 0.0)},
    }
    con.execute(
        "INSERT INTO kpi_snapshots (id, captured_at, window_days, metrics_json) VALUES (?, ?, 30, ?)",
        (new_id(), _hours_ago(hours_ago), json.dumps(payload, sort_keys=True)),
    )
    con.commit()


class ComputeShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_empty_db_review_has_only_no_goals_and_ok(self) -> None:
        rev = compute_review(self.con, window_days=7)
        self.assertEqual(rev["window_days"], 7)
        self.assertEqual(rev["pipeline"]["drafts_created"], 0)
        self.assertEqual(rev["state"]["active_goals"], 0)
        # at least the "no goals" finding fires
        headlines = [f["headline"] for f in rev["findings"]]
        self.assertTrue(any("No active goals" in h for h in headlines))


class IdleDraftsRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        # Suppress competing rules: add an active goal so no_goals doesn't fire.
        create_goal(self.con, title="X", cadence="weekly")

    def test_idle_drafts_critical_after_24h(self) -> None:
        pid = upsert_person(self.con, full_name="A", email="a@x.com")
        _seed_draft(self.con, pid, created_h_ago=30)
        rev = compute_review(self.con, window_days=7)
        criticals = [f for f in rev["findings"] if f["severity"] == SEV_CRITICAL and "drafts queued" in f["headline"]]
        self.assertEqual(len(criticals), 1)
        self.assertIn("approve-draft", criticals[0]["command"])

    def test_no_idle_drafts_finding_when_decisions_exist(self) -> None:
        pid = upsert_person(self.con, full_name="A", email="a@x.com")
        _seed_draft(self.con, pid, status="approved", created_h_ago=30)
        rev = compute_review(self.con, window_days=7)
        idle = [f for f in rev["findings"] if "drafts queued" in f["headline"]]
        self.assertEqual(idle, [])


class StaleSyncRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        create_goal(self.con, title="X", cadence="weekly")

    def test_recent_sync_does_not_fire(self) -> None:
        # Insert a source_run row 1 hour ago.
        ts = _hours_ago(1)
        self.con.execute(
            "INSERT INTO source_runs (id, source, status, stats_json, started_at, finished_at) VALUES (?, 'google_people', 'ok', '{}', ?, ?)",
            (new_id(), ts, ts),
        )
        self.con.commit()
        rev = compute_review(self.con, window_days=7)
        stale = [f for f in rev["findings"] if "Google sync stale" in f["headline"]]
        self.assertEqual(stale, [])

    def test_old_sync_fires_critical(self) -> None:
        ts = _hours_ago(72)
        self.con.execute(
            "INSERT INTO source_runs (id, source, status, stats_json, started_at, finished_at) VALUES (?, 'google_people', 'ok', '{}', ?, ?)",
            (new_id(), ts, ts),
        )
        self.con.commit()
        rev = compute_review(self.con, window_days=7)
        stale = [f for f in rev["findings"] if "Google sync stale" in f["headline"]]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["severity"], SEV_CRITICAL)


class KPIDeltasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        create_goal(self.con, title="X", cadence="weekly")

    def test_two_snapshots_in_window_emit_deltas(self) -> None:
        _seed_kpi_snapshot(self.con, hours_ago=120, total_people=100, stale_high_value=50)
        _seed_kpi_snapshot(self.con, hours_ago=2, total_people=120, stale_high_value=70)
        rev = compute_review(self.con, window_days=7)
        self.assertIn("deltas", rev["kpi"])
        # Newest first, oldest second per compute_review's pairs (current, prev)
        curr, prev = rev["kpi"]["deltas"]["total_people"]
        self.assertEqual(curr, 120)
        self.assertEqual(prev, 100)
        md = render_review_markdown(rev)
        self.assertIn("## KPI deltas (within window)", md)
        self.assertIn("(+20)", md)


class MemoryAndOutcomeRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        create_goal(self.con, title="X", cadence="weekly")

    def test_gbrain_coverage_rule_fires_after_next_actions_without_context(self) -> None:
        upsert_person(self.con, full_name="A", email="a@x.com")
        record_source_run(self.con, source="next_actions", source_ref=None, status="ok", stats={"actions": 1})
        rev = compute_review(self.con, window_days=7)
        findings = [f for f in rev["findings"] if "gbrain context covers" in f["headline"]]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], SEV_ATTENTION)

    def test_linkedin_published_without_outcome_rule_fires(self) -> None:
        draft_id = create_custom_draft(self.con, channel="linkedin_post", subject="Post", body="Body")
        apply_draft_event(self.con, draft_id=draft_id, event_type="published")
        rev = compute_review(self.con, window_days=7)
        findings = [f for f in rev["findings"] if "published LinkedIn post" in f["headline"]]
        self.assertEqual(len(findings), 1)
        self.assertIn("record-engagement-outcome", findings[0]["command"])


class RenderMarkdownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_markdown_has_all_required_sections(self) -> None:
        rev = compute_review(self.con, window_days=7)
        md = render_review_markdown(rev)
        for header in (
            "# Network Chief Agent Review (7-day retrospective)",
            "## Summary",
            "## Activity log (last 7d)",
            "## Pipeline throughput (last 7d)",
            "## KPI deltas (within window)",
            "## Findings & recommendations",
        ):
            self.assertIn(header, md)


class SnapshotRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_save_and_load(self) -> None:
        rev = compute_review(self.con, window_days=7)
        rid = save_review(self.con, rev)
        self.assertTrue(rid)
        loaded = previous_review(self.con, window_days=7)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["window_days"], 7)
        # Different window should not match.
        self.assertIsNone(previous_review(self.con, window_days=30))


if __name__ == "__main__":
    unittest.main()
