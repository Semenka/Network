import unittest

from network_chief.db import (
    add_connection_value,
    add_interaction,
    connect,
    create_goal,
    init_db,
    now_iso,
    upsert_person,
)
from network_chief.gbrain import GBrainResult
from network_chief.next_actions import build_next_actions, format_next_actions


class FakeGBrain:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 5) -> list[GBrainResult]:
        self.queries.append(query)
        if "Alice" not in query:
            return []
        return [GBrainResult(slug="people/alice-energy", score=0.91, text="Alice knows AI in energy operations.")]

    def query(self, query: str, *, limit: int = 5) -> list[GBrainResult]:
        return []


class NextActionsGBrainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_next_actions_mix_relationship_audience_and_gbrain_context(self) -> None:
        create_goal(
            self.con,
            title="Grow AI energy network",
            cadence="weekly",
            capital_type="competence",
            target_segment="AI energy operators",
        )
        person_id = upsert_person(
            self.con,
            full_name="Alice Energy",
            email="alice@example.com",
            linkedin_url="https://linkedin.com/in/alice-energy",
            notes="AI energy operator and speaker.",
        )
        add_connection_value(
            self.con,
            person_id=person_id,
            value_type="competence",
            description="AI operations expertise",
            score=88,
        )
        add_interaction(
            self.con,
            person_id=person_id,
            channel="gmail",
            direction="incoming",
            subject="AI in energy",
            body_summary="Asked about a field deployment",
            occurred_at=now_iso(),
        )

        actions = build_next_actions(self.con, limit=5, adapter=FakeGBrain(), use_gbrain=True)
        rendered = format_next_actions(actions)

        self.assertTrue(any(action.person_id == person_id and action.action_type == "warm_reply" for action in actions))
        self.assertIn("people/alice-energy", rendered)
        fact = self.con.execute(
            "SELECT * FROM source_facts WHERE person_id = ? AND fact_type = 'gbrain_context'",
            (person_id,),
        ).fetchone()
        self.assertIsNotNone(fact)
        run = self.con.execute("SELECT * FROM source_runs WHERE source = 'next_actions'").fetchone()
        self.assertIsNotNone(run)


if __name__ == "__main__":
    unittest.main()
