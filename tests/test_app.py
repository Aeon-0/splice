import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app


class UploadRequest:
    def __init__(self, body, boundary="ugc-boundary", chunk_size=1):
        self.body = body
        self.chunk_size = chunk_size
        self.headers = {"content-type": f'multipart/form-data; boundary="{boundary}"'}

    async def stream(self):
        for offset in range(0, len(self.body), self.chunk_size):
            yield self.body[offset:offset + self.chunk_size]


def multipart(payload, ending=b"--\r\n"):
    return b'--ugc-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.mp4"\r\nContent-Type: video/mp4\r\n\r\n' + payload + b"\r\n--ugc-boundary" + ending


class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.DATA.mkdir(exist_ok=True)

    def test_binary_upload_across_every_boundary_split(self):
        payload = b"\x00\xffsome video\r\n--ugc-not-the-boundary\x00"
        with tempfile.TemporaryDirectory(dir=app.DATA) as directory:
            for size in (1, 2, 7, 19, 4096):
                path = Path(directory) / f"{size}.upload"
                asyncio.run(app.receive_upload(UploadRequest(multipart(payload), chunk_size=size), path))
                self.assertEqual(path.read_bytes(), payload)

    def test_incomplete_and_multi_file_uploads_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=app.DATA) as directory:
            for index, ending in enumerate((b"", b"\r\nContent-Disposition: form-data")):
                with self.assertRaises(HTTPException):
                    asyncio.run(app.receive_upload(UploadRequest(multipart(b"video", ending)), Path(directory) / str(index)))

    def test_upload_size_limit(self):
        with tempfile.TemporaryDirectory(dir=app.DATA) as directory:
            with patch.object(app, "MAX_BYTES", 3), self.assertRaises(HTTPException) as caught:
                asyncio.run(app.receive_upload(UploadRequest(multipart(b"four")), Path(directory) / "upload"))
            self.assertEqual(caught.exception.status_code, 413)


class ValidationTests(unittest.TestCase):
    def test_instagram_urls_are_canonical_and_restricted(self):
        self.assertEqual(app.instagram_url("https://instagram.com/reel/abc_12/?igsh=example"), "https://www.instagram.com/reel/abc_12/")
        for value in ("http://instagram.com/reel/a/", "https://example.com/reel/a/", "https://instagram.com.evil.test/reel/a/", "https://user@instagram.com/reel/a/", "https://instagram.com:443/reel/a/", "https://instagram.com/p/a/", "https://instagram.com:bad/reel/a/"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                app.instagram_url(value)

    def test_paths_cannot_escape_or_open_hidden_files(self):
        for value in ("../outside.mp4", ".git/anything.mp4", ".env", "README.md"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                app.local_video(value)

    def test_cookies_inside_repo_are_rejected_without_reading_them(self):
        with self.assertRaises(HTTPException):
            app.cookies_file(str(app.ROOT / "cookies.txt"))

    def test_api_validation_and_cross_origin_guard(self):
        with TestClient(app.app) as client:
            self.assertEqual(client.post("/api/analyze", json={}).status_code, 400)
            self.assertEqual(client.post("/api/analyze", json={"url": "https://example.com"}).status_code, 400)
            self.assertEqual(client.post("/api/analyze", json={"filePath": "../outside.mp4"}).status_code, 400)
            self.assertEqual(client.get("/api/jobs/missing").status_code, 404)
            self.assertEqual(client.post("/api/analyze", json={}, headers={"Origin": "https://other.test"}).status_code, 403)
            self.assertEqual(client.post("/api/analyze", json={}, headers={"Origin": "http://testserver"}).status_code, 400)
            self.assertEqual(client.get("/media/../jobs/example/source.upload").status_code, 404)

    def test_reel_requests_require_upload_before_cookies_or_job_creation(self):
        with TestClient(app.app) as client, \
                patch.object(app, "cookies_file") as cookies, \
                patch.object(app, "new_job") as new_job, \
                patch.object(app, "download_reel") as download:
            for url in ("https://www.instagram.com/reel/abc_12/", "http://instagram.com/reel/abc_12/", "https://example.com/video.mp4"):
                with self.subTest(url=url):
                    response = client.post("/api/analyze", json={"url": url})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("Upload", response.json()["detail"])
                    self.assertIn("/api/analyze-file", response.json()["detail"])
            cookies.assert_not_called()
            new_job.assert_not_called()
            download.assert_not_called()


class TimelineTests(unittest.TestCase):
    def test_ffmpeg_boundary_debounce_and_endpoints(self):
        detected = "pts_time:0.01 pts_time:1.25 pts_time:1.26 pts_time:2.5 pts_time:3.74"
        with patch.object(app, "run", return_value=SimpleNamespace(stderr=detected)):
            self.assertEqual(app.cut_boundaries(Path("unused.mp4"), 3.75), [0.0, 1.25, 2.5, 3.75])

    def test_no_hard_cut_produces_one_clip(self):
        with patch.object(app, "run", return_value=SimpleNamespace(stderr="")):
            self.assertEqual(app.cut_boundaries(Path("unused.mp4"), 4.5), [0.0, 4.5])

    def test_word_timestamps_are_assigned_once_across_a_cut(self):
        cuts = [{"start": 0, "end": 1}, {"start": 1, "end": 2}]
        words = [SimpleNamespace(start=0.1, end=0.5, word=" Hello"), SimpleNamespace(start=0.8, end=1.2, word=" there"), SimpleNamespace(start=1.3, end=1.9, word=" friend.")]
        segment = SimpleNamespace(words=words)
        self.assertEqual(app.slice_transcript([segment], cuts), ["Hello", "there friend."])

    def test_segment_fallback_without_words(self):
        segment = SimpleNamespace(start=0.2, end=0.8, text=" A sentence.", words=None)
        self.assertEqual(app.slice_transcript([segment], [{"start": 0, "end": 1}]), ["A sentence."])


if __name__ == "__main__":
    unittest.main()
