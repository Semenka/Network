import unittest

from network_chief.cleanup import find_misclassified, delete_people
from network_chief.db import connect, get_or_create_org, init_db, upsert_person


class CleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        # Real person — should NOT be flagged.
        self.real_id = upsert_person(self.con, full_name="Alice Investor", email="alice@vc.example")
        # Email-as-name — should be flagged.
        self.email_id = upsert_person(self.con, full_name="vincenzo@example.com", email=None)
        # Domain-as-name — should be flagged.
        self.domain_id = upsert_person(self.con, full_name="Booking.com")
        # Org name with no other identity — flagged.
        get_or_create_org(self.con, "Electric")
        self.org_id = upsert_person(self.con, full_name="Electric")
        # Org name BUT with a personal LinkedIn URL — NOT flagged (could be real person).
        self.namesake_id = upsert_person(
            self.con, full_name="Apple", linkedin_url="https://linkedin.com/in/apple-cooper"
        )
        # Company-page LinkedIn URL — flagged regardless of name.
        self.company_url_id = upsert_person(
            self.con, full_name="Acme", linkedin_url="https://linkedin.com/company/acme"
        )

    def test_classification(self) -> None:
        flagged = {row["id"]: row["reason"] for row in find_misclassified(self.con)}
        self.assertNotIn(self.real_id, flagged)
        self.assertNotIn(self.namesake_id, flagged)
        self.assertEqual(flagged.get(self.email_id), "email-as-name")
        self.assertEqual(flagged.get(self.domain_id), "domain-as-name")
        self.assertEqual(flagged.get(self.org_id), "organization-as-name")
        self.assertEqual(flagged.get(self.company_url_id), "company-linkedin-url")

    def test_delete_people_cascades(self) -> None:
        flagged = find_misclassified(self.con)
        ids = [row["id"] for row in flagged]
        removed = delete_people(self.con, ids)
        self.assertEqual(removed, len(ids))
        # Real person + namesake should remain.
        remaining = [row[0] for row in self.con.execute("SELECT id FROM people").fetchall()]
        self.assertIn(self.real_id, remaining)
        self.assertIn(self.namesake_id, remaining)


if __name__ == "__main__":
    unittest.main()
