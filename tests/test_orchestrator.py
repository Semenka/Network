import tempfile
import unittest
from unittest import mock

from network_chief import orchestrator
from network_chief.auth.errors import AuthRequired
from network_chief.db import connect, init_db
from network_chief.policy import AutonomyPolicy


_PHASE_FNS = [
    "sync_google_contacts", "sync_gmail_messages", "detect_replies_heuristic",
    "sync_x_following", "sync_x_mentions", "maintain_connection_values",
    "rank_people", "build_daily_brief", "prepare_gmail_keepalive",
    "prepare_telegram_keepalive", "prepare_linkedin_posts", "prepare_x_posts",
    "prepare_x_comments", "push_drafts_to_gmail", "send_drafts_via_gmail",
    "compute_dashboard", "previous_snapshot", "save_snapshot", "render_markdown",
    "compute_review", "previous_review", "save_review", "render_review_markdown",
]


def _patch_all(stack, *, review_findings=None, send_result=None):
    """Replace every phase function in orchestrator's namespace with a stub."""
    for name in _PHASE_FNS:
        if not hasattr(orchestrator, name):
            continue
        if name == "compute_review":
            stack.enter_context(mock.patch.object(
                orchestrator, name,
                return_value={"findings": review_findings or []},
            ))
        elif name == "send_drafts_via_gmail":
            stack.enter_context(mock.patch.object(
                orchestrator, name,
                return_value=send_result or {"sent": 0, "dry_run": 0, "blocked": 0, "errors": []},
            ))
        elif name == "rank_people":
            stack.enter_context(mock.patch.object(orchestrator, name, return_value=[]))
        elif name in ("render_markdown", "render_review_markdown"):
            stack.enter_context(mock.patch.object(orchestrator, name, return_value="# md"))
        elif name in ("previous_snapshot", "previous_review"):
            stack.enter_context(mock.patch.object(orchestrator, name, return_value=None))
        elif name == "compute_dashboard":
            stack.enter_context(mock.patch.object(orchestrator, name, return_value={"x": 1}))
        else:
            stack.enter_context(mock.patch.object(orchestrator, name, return_value={"ok": True}))


class RunCycleTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cycle_runs_all_phases_and_logs(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            _patch_all(stack)
            res = orchestrator.run_cycle(self.con, policy=AutonomyPolicy(), dashboards_dir=self.tmp.name)
        phases = {s["phase"] for s in res["steps"]}
        self.assertEqual({"sense", "think", "act", "report"} & phases, {"sense", "think", "act", "report"})
        # source_runs has an autopilot.* row per phase.
        rows = [r[0] for r in self.con.execute(
            "SELECT source FROM source_runs WHERE source LIKE 'autopilot.%'").fetchall()]
        for phase in ("autopilot.sense", "autopilot.think", "autopilot.act", "autopilot.report"):
            self.assertIn(phase, rows)

    def test_sense_token_failure_does_not_crash(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            _patch_all(stack)
            stack.enter_context(mock.patch.object(
                orchestrator, "sync_google_contacts", side_effect=AuthRequired("no token")))
            res = orchestrator.run_cycle(self.con, policy=AutonomyPolicy(), dashboards_dir=self.tmp.name)
        skipped = [s for s in res["steps"] if s["status"] == "skipped" and s["step"] == "google_contacts"]
        self.assertEqual(len(skipped), 1)

    def test_self_direction_acts_on_stale_backlog(self):
        import contextlib
        finding = {"severity": "critical", "rule": "_r_stale_backlog", "headline": "backlog!"}
        with contextlib.ExitStack() as stack:
            _patch_all(stack, review_findings=[finding])
            # Spy on the auto-action's targets.
            spy_brief = stack.enter_context(mock.patch.object(orchestrator, "build_daily_brief", return_value="x"))
            spy_keep = stack.enter_context(mock.patch.object(orchestrator, "prepare_gmail_keepalive", return_value=["a"]))
            res = orchestrator.run_cycle(self.con, policy=AutonomyPolicy(), dashboards_dir=self.tmp.name)
        self.assertTrue(any(a["rule"] == "_r_stale_backlog" for a in res["auto_actions"]))
        self.assertTrue(spy_brief.called and spy_keep.called)

    def test_judgement_finding_not_auto_actioned(self):
        import contextlib
        finding = {"severity": "critical", "rule": "_r_idle_drafts", "headline": "decide!", "command": "network-chief drafts"}
        with contextlib.ExitStack() as stack:
            _patch_all(stack, review_findings=[finding])
            res = orchestrator.run_cycle(self.con, policy=AutonomyPolicy(), dashboards_dir=self.tmp.name)
        self.assertEqual(res["auto_actions"], [])
        digest = orchestrator.render_operator_digest(res, AutonomyPolicy())
        self.assertIn("Needs your decision", digest)
        self.assertIn("network-chief drafts", digest)


if __name__ == "__main__":
    unittest.main()
