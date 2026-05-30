import unittest

from network_chief.dashboard import compute_dashboard, render_markdown
from network_chief.db import (
    add_goal_milestone,
    connect,
    create_goal,
    init_db,
    list_goal_milestones,
    new_id,
    now_iso,
    update_goal_milestone,
    upsert_person,
)
from network_chief.drafts import set_draft_status
from network_chief.review import compute_review


class MilestoneTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)
        self.goal_id = create_goal(self.con, title="Reach investors", cadence="weekly")

    def test_add_update_list(self):
        mid = add_goal_milestone(
            self.con, goal_id=self.goal_id, metric_name="warm intros", target_value=5, current_value=2
        )
        rows = list_goal_milestones(self.con, self.goal_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["current_value"], 2)
        self.assertTrue(update_goal_milestone(self.con, mid, current_value=4))
        self.assertEqual(list_goal_milestones(self.con, self.goal_id)[0]["current_value"], 4)

    def test_dashboard_renders_progress(self):
        add_goal_milestone(
            self.con, goal_id=self.goal_id, metric_name="warm intros", target_value=5, current_value=3
        )
        snap = compute_dashboard(self.con, window_days=30)
        md = render_markdown(snap)
        self.assertIn("warm intros: 3/5 (60%)", md)


class RejectionReasonTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)

    def _make_draft(self, pid):
        did = new_id()
        ts = now_iso()
        self.con.execute(
            "INSERT INTO drafts (id, person_id, channel, body, status, created_at, updated_at) "
            "VALUES (?, ?, 'gmail', 'b', 'draft', ?, ?)",
            (did, pid, ts, ts),
        )
        self.con.commit()
        return did

    def test_reason_persisted_on_reject(self):
        pid = upsert_person(self.con, full_name="A", email="a@x.com")
        did = self._make_draft(pid)
        set_draft_status(self.con, did, "rejected", reason="wrong_timing")
        row = self.con.execute("SELECT status, rejection_reason FROM drafts WHERE id=?", (did,)).fetchone()
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["rejection_reason"], "wrong_timing")

    def test_review_flags_dominant_reason(self):
        # 4 rejected, 3 of them 'wrong_timing' (75% > 30%).
        for i, reason in enumerate(["wrong_timing", "wrong_timing", "wrong_timing", "weak_context"]):
            pid = upsert_person(self.con, full_name=f"P{i}", email=f"p{i}@x.com")
            did = self._make_draft(pid)
            set_draft_status(self.con, did, "rejected", reason=reason)
        rev = compute_review(self.con, window_days=7)
        flagged = [f for f in rev["findings"] if "rejections are 'wrong_timing'" in f["headline"]]
        self.assertEqual(len(flagged), 1)


if __name__ == "__main__":
    unittest.main()
