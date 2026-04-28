import unittest

from network_chief.db import connect, create_goal, init_db, upsert_person
from network_chief.engagement import prepare_gmail_keepalive, prepare_linkedin_posts, prepare_x_comments, prepare_x_posts


class EngagementTest(unittest.TestCase):
    def test_prepare_channel_specific_drafts(self):
        con = connect(":memory:")
        init_db(con)
        upsert_person(con, full_name="Alice Email", email="alice@example.com")
        upsert_person(con, full_name="Bob X", twitter_handle="@bobx")
        create_goal(
            con,
            title="Grow AI operator network",
            cadence="weekly",
            capital_type="competence",
            target_segment="AI operators",
        )

        gmail_ids = prepare_gmail_keepalive(con, limit=1)
        linkedin_ids = prepare_linkedin_posts(con, count=2)
        x_post_ids = prepare_x_posts(con, count=1)
        x_comment_ids = prepare_x_comments(con, count=1)

        self.assertEqual(len(gmail_ids), 1)
        self.assertEqual(len(linkedin_ids), 2)
        self.assertEqual(len(x_post_ids), 1)
        self.assertEqual(len(x_comment_ids), 1)
        channels = {row["channel"] for row in con.execute("SELECT channel FROM drafts")}
        self.assertEqual(channels, {"gmail", "linkedin_post", "x_post", "x_comment"})


if __name__ == "__main__":
    unittest.main()
