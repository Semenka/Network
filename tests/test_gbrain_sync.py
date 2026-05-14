import unittest

from network_chief.db import connect, init_db, upsert_person
from network_chief.drafts import apply_draft_event, create_draft, set_draft_status
from network_chief.gbrain import GBrainAdapter, GBrainResult, fetch_gbrain_context, sync_gbrain_summaries


class FakeWritebackGBrain:
    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.timeline: list[tuple[str, str, str]] = []
        self.synced = False

    def get_page(self, slug: str) -> str | None:
        return self.pages.get(slug)

    def put_page(self, slug: str, content: str, *, dry_run: bool = False) -> dict[str, object]:
        if not dry_run:
            self.pages[slug] = content
        return {"status": "dry_run" if dry_run else "ok", "slug": slug}

    def add_timeline_entry(self, slug: str, date: str, text: str, *, dry_run: bool = False) -> dict[str, object]:
        if not dry_run:
            self.timeline.append((slug, date, text))
        return {"status": "dry_run" if dry_run else "ok", "slug": slug}

    def sync(self, *, dry_run: bool = False) -> dict[str, object]:
        self.synced = not dry_run
        return {"status": "dry_run" if dry_run else "ok"}


class GBrainSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)

    def test_fetch_context_combines_search_and_query(self) -> None:
        def runner(args, stdin):
            command = args[1]
            if command == "search":
                return _completed("[0.91] people/a -- Alpha context\n")
            if command == "query":
                return _completed("[0.72] people/b -- Beta context\n")
            return _completed("")

        results = fetch_gbrain_context("alpha", adapter=GBrainAdapter(runner=runner), limit=3)
        self.assertEqual([item.slug for item in results], ["people/a", "people/b"])
        self.assertTrue(all(isinstance(item, GBrainResult) for item in results))

    def test_sync_writes_summaries_without_raw_body_and_is_idempotent(self) -> None:
        person_id = upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")
        draft_id = create_draft(self.con, person={"id": person_id, "full_name": "Alice Energy", "primary_email": "alice@example.com"})
        secret_body = "SECRET PRIVATE MESSAGE BODY SHOULD NOT BE WRITTEN"
        self.con.execute("UPDATE drafts SET body = ?, subject = ? WHERE id = ?", (secret_body, "Catch up", draft_id))
        self.con.commit()
        set_draft_status(self.con, draft_id, "approved", reason_code="good_context")
        apply_draft_event(self.con, draft_id=draft_id, event_type="sent", note="Short catch-up sent")

        adapter = FakeWritebackGBrain()
        stats = sync_gbrain_summaries(self.con, adapter=adapter, since_days=7)
        again = sync_gbrain_summaries(self.con, adapter=adapter, since_days=7)

        self.assertEqual(stats["written"], 2)
        self.assertEqual(again["written"], 0)
        self.assertGreaterEqual(again["skipped"], 2)
        self.assertTrue(adapter.synced)
        combined_pages = "\n".join(adapter.pages.values())
        self.assertIn("Network Chief", combined_pages)
        self.assertNotIn(secret_body, combined_pages)

    def test_dry_run_does_not_mark_writeback_done(self) -> None:
        person_id = upsert_person(self.con, full_name="Alice Energy", email="alice@example.com")
        draft_id = create_draft(
            self.con,
            person={"id": person_id, "full_name": "Alice Energy", "primary_email": "alice@example.com"},
        )
        set_draft_status(self.con, draft_id, "approved", reason_code="good_context")

        stats = sync_gbrain_summaries(self.con, adapter=FakeWritebackGBrain(), since_days=7, dry_run=True)

        self.assertEqual(stats["written"], 1)
        fact = self.con.execute("SELECT * FROM source_facts WHERE fact_type = 'gbrain_writeback'").fetchone()
        self.assertIsNone(fact)


def _completed(stdout: str):
    import subprocess

    return subprocess.CompletedProcess(["gbrain"], 0, stdout, "")


if __name__ == "__main__":
    unittest.main()
