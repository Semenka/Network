import unittest

from network_chief.db import (
    add_connection_value,
    add_interaction,
    connect,
    get_or_create_org,
    add_role,
    init_db,
    set_consent_status,
    upsert_person,
)
from network_chief.cleanup import merge_people
from network_chief.drafts import compose_draft
from network_chief.scoring import rank_people


class ConsentTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)

    def test_opted_out_excluded_from_ranking(self):
        keep = upsert_person(self.con, full_name="Active Anna", email="anna@x.com")
        drop = upsert_person(self.con, full_name="Opted Olga", email="olga@x.com")
        for pid in (keep, drop):
            add_connection_value(self.con, person_id=pid, value_type="competence", description="op", score=80)
        set_consent_status(self.con, status="opted_out", email="olga@x.com")
        ranked_ids = {p["id"] for p in rank_people(self.con, limit=10)}
        self.assertIn(keep, ranked_ids)
        self.assertNotIn(drop, ranked_ids)

    def test_set_consent_no_match(self):
        res = set_consent_status(self.con, status="paused", email="ghost@x.com")
        self.assertFalse(res["matched"])

    def test_invalid_status_rejected(self):
        upsert_person(self.con, full_name="A", email="a@x.com")
        res = set_consent_status(self.con, status="banned", email="a@x.com")
        self.assertFalse(res["matched"])


class MergePeopleTest(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")
        init_db(self.con)

    def test_merge_repoints_history_and_backfills(self):
        # Two genuinely distinct rows (different emails, so upsert_person won't auto-merge).
        primary = upsert_person(self.con, full_name="Dmitry Dumik", email="ddumik@work.com")
        dup = upsert_person(self.con, full_name="Dmitry Dumik", email="dmitry@dumik.com",
                            linkedin_url="https://linkedin.com/in/ddumik")
        self.assertNotEqual(primary, dup)
        # History on the duplicate.
        org = get_or_create_org(self.con, "Chatfuel")
        add_role(self.con, person_id=dup, organization_id=org, title="Founder")
        add_interaction(self.con, person_id=dup, channel="gmail", direction="incoming",
                        occurred_at="2026-05-01T00:00:00Z", source="s", source_ref="r1")
        add_connection_value(self.con, person_id=dup, value_type="competence", description="Founder", score=70)

        result = merge_people(self.con, primary_id=primary, duplicate_ids=[dup])
        self.assertEqual(result["merged"], 1)
        # Duplicate gone.
        self.assertIsNone(self.con.execute("SELECT 1 FROM people WHERE id=?", (dup,)).fetchone())
        # linkedin_url backfilled onto primary (primary kept its own email).
        row = self.con.execute("SELECT primary_email, linkedin_url FROM people WHERE id=?", (primary,)).fetchone()
        self.assertEqual(row["primary_email"], "ddumik@work.com")
        self.assertEqual(row["linkedin_url"], "https://linkedin.com/in/ddumik")
        # History re-pointed.
        self.assertEqual(self.con.execute("SELECT count(*) FROM roles WHERE person_id=?", (primary,)).fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM interactions WHERE person_id=?", (primary,)).fetchone()[0], 1)

    def test_merge_ignores_self_and_missing(self):
        primary = upsert_person(self.con, full_name="Solo", email="solo@x.com")
        result = merge_people(self.con, primary_id=primary, duplicate_ids=[primary])
        self.assertEqual(result["merged"], 0)


class MergeFieldDraftTest(unittest.TestCase):
    def test_last_subject_merged_into_body(self):
        person = {
            "full_name": "Maria Konovalenko",
            "organizations": "Buck Institute",
            "titles": "PhD student",
            "last_interaction_subject": "longevity research collab",
            "staleness_days": 200,
        }
        out = compose_draft(person, channel="gmail")
        self.assertIn("longevity research collab", out["body"])
        # Staleness-aware subject diversifies away from the flat "Quick catch-up".
        self.assertEqual(out["subject"], "Long overdue catch-up")

    def test_telegram_still_short_and_informal(self):
        person = {"full_name": "Bob Builder", "organizations": "Acme", "staleness_days": 10}
        out = compose_draft(person, channel="telegram")
        self.assertIn("Hey Bob", out["body"])
        self.assertNotIn("Best,\nAndrey", out["body"])
        self.assertLess(len(out["body"]), 320)

    def test_title_fallback_when_no_last_subject(self):
        person = {"full_name": "Ann", "organizations": "Initech", "titles": "VP Eng", "staleness_days": 5}
        out = compose_draft(person, channel="gmail")
        self.assertIn("VP Eng", out["body"])
        self.assertEqual(out["subject"], "Quick catch-up")


if __name__ == "__main__":
    unittest.main()
