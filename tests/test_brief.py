import unittest

from network_chief.brief import build_daily_brief
from network_chief.db import add_resource, connect, create_goal, init_db, upsert_person


class BriefTest(unittest.TestCase):
    def test_brief_creates_drafts_for_ranked_people(self):
        con = connect(":memory:")
        init_db(con)
        person_id = upsert_person(con, full_name="Alice Investor", email="alice@example.com")
        add_resource(
            con,
            person_id=person_id,
            resource_type="financial",
            description="VC investor",
            source="test",
            confidence=0.8,
        )
        create_goal(
            con,
            title="Reactivate investor network",
            cadence="weekly",
            capital_type="financial",
            target_segment="investor",
        )

        brief = build_daily_brief(con, limit=1)
        build_daily_brief(con, limit=1)

        self.assertIn("Alice Investor", brief)
        self.assertEqual(con.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
