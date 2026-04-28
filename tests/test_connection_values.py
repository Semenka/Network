import unittest

from network_chief.db import connect, init_db, list_connection_values, upsert_person
from network_chief.value import maintain_connection_values


class ConnectionValueTest(unittest.TestCase):
    def test_maintain_connection_values_extracts_requested_value_types(self):
        con = connect(":memory:")
        init_db(con)
        upsert_person(
            con,
            full_name="Alice Operator",
            notes="AI investor, automation operator, and machine learning expert.",
            confidence=0.7,
        )

        stats = maintain_connection_values(con)
        values = list_connection_values(con)
        value_types = {value["value_type"] for value in values}

        self.assertEqual(stats["people_scanned"], 1)
        self.assertGreaterEqual(stats["values_seen"], 3)
        self.assertIn("financial_capital", value_types)
        self.assertIn("time_saving", value_types)
        self.assertIn("specific_knowledge", value_types)


if __name__ == "__main__":
    unittest.main()
