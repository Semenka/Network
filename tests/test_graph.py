import unittest

from network_chief.db import (
    add_connection_value,
    add_role,
    connect,
    get_or_create_org,
    init_db,
    upsert_person,
)
from network_chief.graph import render_graph_markdown, render_mermaid


class GraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_empty_db_renders_placeholder(self) -> None:
        out = render_mermaid(self.con)
        self.assertIn("graph LR", out)
        self.assertIn("No contacts yet", out)

    def test_top_people_rendered_with_classes_and_orgs(self) -> None:
        alice = upsert_person(self.con, full_name="Alice Investor", email="alice@vc.example")
        bob = upsert_person(self.con, full_name="Bob Builder", email="bob@example.com")
        org = get_or_create_org(self.con, "Acme Ventures")
        add_role(self.con, person_id=alice, organization_id=org, title="Partner")
        add_connection_value(
            self.con,
            person_id=alice,
            value_type="financial_capital",
            description="Investor",
            score=85,
            source="seed",
        )
        add_connection_value(
            self.con,
            person_id=bob,
            value_type="competence",
            description="Founder",
            score=70,
            source="seed",
        )

        out = render_mermaid(self.con, limit=10)
        self.assertIn("subgraph financial_capital", out)
        self.assertIn("subgraph competence", out)
        self.assertIn("Alice Investor", out)
        self.assertIn("Bob Builder", out)
        self.assertIn("Acme Ventures", out)
        # Both no-touch + ≥60 value → redalert class
        self.assertIn("redalert", out)
        # Edge from person → org with role label
        self.assertIn('|"Partner"|', out)

    def test_graph_markdown_wraps_in_mermaid_fence(self) -> None:
        upsert_person(self.con, full_name="Solo Contact")
        md = render_graph_markdown(self.con, limit=5)
        self.assertIn("## Network graph (top 5 by value-score)", md)
        self.assertIn("```mermaid", md)
        self.assertIn("```\n", md)


if __name__ == "__main__":
    unittest.main()
