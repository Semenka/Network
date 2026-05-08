import json
import unittest

from network_chief.dashboard import (
    compute_dashboard,
    previous_snapshot,
    render_markdown,
    save_snapshot,
)
from network_chief.db import (
    add_connection_value,
    add_interaction,
    connect,
    create_goal,
    init_db,
    record_source_run,
    upsert_person,
)
from network_chief.db import new_id, now_iso
from network_chief.drafts import set_draft_status


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        self._seed()

    def _seed(self) -> None:
        alice = upsert_person(self.con, full_name="Alice Investor", email="alice@vc.example")
        bob = upsert_person(self.con, full_name="Bob Builder", email="bob@example.com")
        upsert_person(self.con, full_name="Carol Cold", email="carol@example.com")
        # Recent interactions for two people, none for Carol
        add_interaction(
            self.con,
            person_id=alice,
            channel="gmail",
            direction="incoming",
            occurred_at="2026-04-25T10:00:00Z",
            source="gmail_api",
            source_ref="m1",
        )
        add_interaction(
            self.con,
            person_id=alice,
            channel="gmail",
            direction="outgoing",
            occurred_at="2026-04-26T10:00:00Z",
            source="gmail_api",
            source_ref="m2",
        )
        add_interaction(
            self.con,
            person_id=bob,
            channel="gmail",
            direction="incoming",
            occurred_at="2026-04-27T10:00:00Z",
            source="gmail_api",
            source_ref="m3",
        )
        # High-value signal on Alice (so cadence works); Carol gets a high-value signal
        # WITHOUT a recent touch → stale-but-valuable should be 1.
        add_connection_value(
            self.con,
            person_id=alice,
            value_type="financial_capital",
            description="Investor",
            score=80,
            source="seed",
        )
        cold_id = self.con.execute("SELECT id FROM people WHERE full_name = 'Carol Cold'").fetchone()[0]
        add_connection_value(
            self.con,
            person_id=cold_id,
            value_type="specific_knowledge",
            description="ML researcher",
            score=75,
            source="seed",
        )

        # Drafts: 2 created, 1 approved, 1 rejected → approval_rate 50%
        ts = now_iso()
        d1, d2 = new_id(), new_id()
        for draft_id, person_id, channel in ((d1, alice, "gmail"), (d2, bob, "linkedin")):
            self.con.execute(
                """
                INSERT INTO drafts (id, person_id, channel, body, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'draft', ?, ?)
                """,
                (draft_id, person_id, channel, "stub body", ts, ts),
            )
        self.con.commit()
        set_draft_status(self.con, d1, "approved")
        set_draft_status(self.con, d2, "rejected")

        record_source_run(
            self.con, source="gmail_api", source_ref=None, status="ok",
            stats={"messages_seen": 3},
        )

        create_goal(
            self.con, title="Reach investors", cadence="weekly",
            capital_type="financial_capital", target_segment="investor",
        )

    def test_compute_dashboard_shape(self) -> None:
        snap = compute_dashboard(self.con, window_days=30)
        self.assertEqual(snap["breadth"]["total_people"], 3)
        self.assertEqual(snap["pipeline"]["drafts_created"], 2)
        self.assertEqual(snap["pipeline"]["drafts_approved"], 1)
        self.assertEqual(snap["pipeline"]["approval_rate_pct"], 50.0)
        self.assertEqual(snap["cadence"]["incoming_90d"], 2)
        self.assertEqual(snap["cadence"]["outgoing_90d"], 1)
        # Carol has high-value signal AND no touches → stale-high-value = 1.
        self.assertEqual(snap["cadence"]["stale_high_value"], 1)
        # Goal coverage matches investor + financial_capital → at least Alice.
        goal = snap["goals"][0]
        self.assertGreaterEqual(goal["matching_people"], 1)

    def test_save_and_previous_snapshot(self) -> None:
        snap = compute_dashboard(self.con, window_days=30)
        save_snapshot(self.con, snap)
        loaded = previous_snapshot(self.con, window_days=30)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["breadth"]["total_people"], 3)
        # Different window should not match.
        self.assertIsNone(previous_snapshot(self.con, window_days=7))

    def test_render_markdown_emits_all_pillars_and_deltas(self) -> None:
        snap = compute_dashboard(self.con, window_days=30)
        # First snapshot → no previous → no deltas.
        first = render_markdown(snap)
        self.assertIn("# Network Chief Dashboard (30-day window)", first)
        for header in (
            "## 1. Network breadth",
            "## 2. Engagement cadence",
            "## 3. Action pipeline",
            "## 4. Value coverage",
            "## 5. Goal coverage",
            "## 6. How to read this",
            "Stale-but-valuable",
        ):
            self.assertIn(header, first)
        save_snapshot(self.con, snap)

        # Add a person and recompute → second render should show +1 delta.
        upsert_person(self.con, full_name="Dana New", email="dana@example.com")
        snap2 = compute_dashboard(self.con, window_days=30)
        prev = previous_snapshot(self.con, window_days=30)
        second = render_markdown(snap2, previous=prev)
        self.assertIn("Total contacts**: 4 (+1)", second)


if __name__ == "__main__":
    unittest.main()
