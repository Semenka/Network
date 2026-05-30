import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from network_chief.auth.tokens import TokenStore
from network_chief.db import connect, init_db
from network_chief.importers import google_api


def _seed_token(con) -> None:
    TokenStore(con).save(
        provider="google",
        account="alice@example.com",
        access_token="ACCESS",
        refresh_token="REFRESH",
        scopes=google_api.DEFAULT_SCOPES + " https://www.googleapis.com/auth/drive",
        expires_at="2099-01-01T00:00:00Z",
        extra={"client_id": "cid"},
    )


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)


class DriveDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = connect(":memory:")
        init_db(self.con)
        _seed_token(self.con)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_csv_file_streams_to_disk_via_alt_media(self) -> None:
        meta = {
            "id": "FILE_ID",
            "name": "Connections.csv",
            "mimeType": "text/csv",
            "size": "12",
        }
        captured: dict[str, str] = {}

        def fake_request_json(method, url, headers=None, params=None, **kwargs):
            captured["meta_url"] = url
            captured["meta_params"] = json.dumps(params, sort_keys=True)
            return meta

        def fake_urlopen(req, timeout=120):
            captured["download_url"] = req.full_url
            captured["auth_header"] = req.get_header("Authorization") or ""
            return _FakeResp(b"first,last\nAlice,VC\n")

        dest = Path(self.tmp.name) / "out.csv"
        with mock.patch("network_chief.importers.google_api.request_json", side_effect=fake_request_json):
            with mock.patch("network_chief.importers.google_api.urllib.request.urlopen", side_effect=fake_urlopen):
                result = google_api.download_drive_file(self.con, file_id="FILE_ID", dest=dest)

        self.assertEqual(result["name"], "Connections.csv")
        self.assertEqual(result["mime_type"], "text/csv")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(), "first,last\nAlice,VC\n")
        self.assertIn("alt=media", captured["download_url"])
        self.assertNotIn("/export", captured["download_url"])
        self.assertEqual(captured["auth_header"], "Bearer ACCESS")

    def test_google_sheet_uses_export_endpoint_with_csv_mime(self) -> None:
        meta = {
            "id": "SHEET_ID",
            "name": "MyContacts",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        }
        captured: dict[str, str] = {}

        def fake_request_json(method, url, headers=None, params=None, **kwargs):
            return meta

        def fake_urlopen(req, timeout=120):
            captured["download_url"] = req.full_url
            return _FakeResp(b"a,b\n1,2\n")

        dest = Path(self.tmp.name) / "exported.csv"
        with mock.patch("network_chief.importers.google_api.request_json", side_effect=fake_request_json):
            with mock.patch("network_chief.importers.google_api.urllib.request.urlopen", side_effect=fake_urlopen):
                google_api.download_drive_file(self.con, file_id="SHEET_ID", dest=dest)

        self.assertIn("/export", captured["download_url"])
        self.assertIn("mimeType=text%2Fcsv", captured["download_url"])
        self.assertEqual(dest.read_text(), "a,b\n1,2\n")

    def test_default_dest_lands_in_exports(self) -> None:
        meta = {"id": "F", "name": "Notes.csv", "mimeType": "text/csv"}
        with mock.patch("network_chief.importers.google_api.request_json", return_value=meta):
            with mock.patch(
                "network_chief.importers.google_api.urllib.request.urlopen",
                return_value=_FakeResp(b"x"),
            ):
                # Run inside a temp cwd so we don't touch the real exports/.
                import os
                old = os.getcwd()
                try:
                    os.chdir(self.tmp.name)
                    result = google_api.download_drive_file(self.con, file_id="F")
                    self.assertTrue(result["path"].endswith("exports/Notes.csv"))
                    self.assertTrue(Path(result["path"]).exists())
                finally:
                    os.chdir(old)


if __name__ == "__main__":
    unittest.main()
