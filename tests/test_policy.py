import unittest
from datetime import UTC, datetime

from network_chief.db import (
    add_interaction,
    connect,
    init_db,
    new_id,
    now_iso,
    upsert_person,
)
from network_chief.policy import AutonomyPolicy, permits_send, load_policy, save_policy


def _draft_row(con, *, email="a@x.com", consent="active", name="A"):
    pid = upsert_person(con, full_name=name, email=email)
    con.execute("UPDATE people SET consent_status = ? WHERE id = ?", (consent, pid))
    con.commit()
    return {"id": new_id(), "person_id": pid, "primary_email": email, "consent_status": consent}


# A daytime instant outside the default quiet hours (21,8).
NOON = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)


class PermitsSendTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)

    def test_level0_always_blocks(self):
        d = _draft_row(self.con)
        ok, reason = permits_send(AutonomyPolicy(level=0), self.con, d, now=NOON)
        self.assertFalse(ok)
        self.assertIn("prepare-only", reason)

    def test_level1_blocks_without_prior_reply(self):
        d = _draft_row(self.con)
        ok, reason = permits_send(AutonomyPolicy(level=1), self.con, d, now=NOON)
        self.assertFalse(ok)
        self.assertIn("prior reply", reason)

    def test_level1_permits_warm_contact_under_cap(self):
        d = _draft_row(self.con)
        add_interaction(self.con, person_id=d["person_id"], channel="gmail",
                        direction="incoming", occurred_at=now_iso(), source="s", source_ref="r1")
        ok, reason = permits_send(AutonomyPolicy(level=1), self.con, d, now=NOON, todays_sends=0)
        self.assertTrue(ok, reason)

    def test_cap_blocks(self):
        d = _draft_row(self.con)
        add_interaction(self.con, person_id=d["person_id"], channel="gmail",
                        direction="incoming", occurred_at=now_iso(), source="s", source_ref="r1")
        ok, reason = permits_send(AutonomyPolicy(level=1, daily_send_cap=5), self.con, d, now=NOON, todays_sends=5)
        self.assertFalse(ok)
        self.assertIn("cap", reason)

    def test_inactive_consent_blocked_even_at_l2(self):
        d = _draft_row(self.con, consent="opted_out")
        ok, reason = permits_send(AutonomyPolicy(level=2), self.con, d, now=NOON)
        self.assertFalse(ok)
        self.assertIn("consent", reason)

    def test_missing_email_blocked(self):
        d = _draft_row(self.con, email="x@y.com")
        d["primary_email"] = ""
        ok, reason = permits_send(AutonomyPolicy(level=2), self.con, d, now=NOON)
        self.assertFalse(ok)
        self.assertIn("address", reason)

    def test_quiet_hours_blocks(self):
        d = _draft_row(self.con)
        night = datetime(2026, 5, 8, 23, 0, 0, tzinfo=UTC)
        ok, reason = permits_send(AutonomyPolicy(level=2), self.con, d, now=night)
        self.assertFalse(ok)
        self.assertIn("quiet", reason)

    def test_level2_permits_first_contact_under_caps(self):
        d = _draft_row(self.con)  # no prior reply
        ok, reason = permits_send(AutonomyPolicy(level=2), self.con, d, now=NOON, todays_sends=0)
        self.assertTrue(ok, reason)

    def test_channel_not_enabled_blocked(self):
        d = _draft_row(self.con)
        ok, reason = permits_send(AutonomyPolicy(level=2, channels_enabled=("gmail",)),
                                  self.con, d, now=NOON, channel="x")
        self.assertFalse(ok)
        self.assertIn("not enabled", reason)


class PolicyPersistenceTest(unittest.TestCase):
    def test_round_trip_json(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "network.db")
            p = AutonomyPolicy(level=2, dry_run=False, daily_send_cap=9, channels_enabled=("gmail", "x"))
            save_policy(p, db)
            loaded = load_policy(db)
            self.assertEqual(loaded.level, 2)
            self.assertFalse(loaded.dry_run)
            self.assertEqual(loaded.daily_send_cap, 9)
            self.assertEqual(loaded.channels_enabled, ("gmail", "x"))

    def test_defaults_are_safe(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "network.db")  # no file yet
            p = load_policy(db)
            self.assertEqual(p.level, 0)
            self.assertTrue(p.dry_run)


if __name__ == "__main__":
    unittest.main()
