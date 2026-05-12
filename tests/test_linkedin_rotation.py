import tempfile
import unittest
from datetime import date
from pathlib import Path

from network_chief.db import connect, create_goal, init_db
from network_chief.linkedin_rotation import prepare_rotating_linkedin_post, preview_rotation


class LinkedInRotationTest(unittest.TestCase):
    def test_preview_rotates_highlight_and_visual_style(self):
        rows = preview_rotation(days=7, start=date(2026, 5, 13))

        self.assertEqual(len(rows), 7)
        self.assertEqual(len({row["rotation"] for row in rows}), 7)
        self.assertEqual(len({row["visual_style"] for row in rows}), 7)

    def test_prepare_rotating_linkedin_post_creates_draft_and_visual(self):
        with tempfile.TemporaryDirectory() as directory:
            con = connect(":memory:")
            init_db(con)
            create_goal(
                con,
                title="Grow energy AI audience",
                cadence="weekly",
                capital_type="competence",
                target_segment="energy operators and AI builders",
            )

            result = prepare_rotating_linkedin_post(
                con,
                post_date=date(2026, 5, 13),
                asset_dir=directory,
                out=Path(directory) / "post.md",
                rotation_index=0,
            )

            draft = con.execute("SELECT channel, body FROM drafts WHERE id = ?", (result["draft_id"],)).fetchone()
            self.assertEqual(draft["channel"], "linkedin_post")
            self.assertIn("TotalEnergies", draft["body"])
            self.assertIn("ADNOC", draft["body"])
            self.assertIn("Comment with one option", draft["body"])
            self.assertTrue(Path(result["svg_path"]).exists())
            self.assertTrue((Path(directory) / "post.md").exists())


if __name__ == "__main__":
    unittest.main()

