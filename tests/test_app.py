import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import create_app
from config import ADMIN_EMAIL, ROOT, Settings
import pipeline
from security import ABSOLUTE_SECONDS, IDLE_SECONDS, LOGIN_ERROR
from transcript import slice_transcript
from uploads import receive_upload, valid_filename

# Synthetic fixtures only; never real credentials or deployment secrets.
PASSWORD = "synthetic-test-password-123"
CSRF = {"X-Requested-With": "Splice"}


class TestPasswords:
    """A deliberately cheap test double; never wired into the running application."""
    def hash(self, password):
        return hashlib.sha256(password.encode()).hexdigest()

    def verify(self, encoded, password):
        return encoded == self.hash(password)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "data").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "data")
        self.settings = Settings(Path(self.temp.name), "https://testserver", "", bytes(32), True, True)
        self.application = create_app(self.settings, TestPasswords())
        with patch.dict(os.environ, {"ADMIN_PASSWORD": PASSWORD}):
            self.client = TestClient(self.application, base_url=self.settings.origin)
            self.client.__enter__()
        self.store = self.application.state.store
        self.auth = self.application.state.auth
        self.users = {}
        with self.store.db() as db:
            for email in ("alice@example.test", "bob@example.test"):
                uid = str(uuid.uuid4())
                db.execute("INSERT INTO users VALUES (?,?,?,1)", (uid, email, TestPasswords().hash(PASSWORD)))
                self.users[email] = uid
            self.users[ADMIN_EMAIL] = db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def login(self, email="alice@example.test", password=PASSWORD):
        return self.client.post("/api/login", json={"email": email, "password": password}, headers=CSRF)

    def fixture_job(self, owner="bob@example.test"):
        uid = self.users[owner]
        job_id = self.store.new_job(uid)
        directory = self.store.directory(uid, job_id)
        (directory / "source.bin").write_bytes(b"synthetic-original")
        (directory / "clip-001.mp4").write_bytes(b"synthetic-video")
        self.store.update(uid, job_id, status="complete", sourceSuffix=".mp4", originalFilename="example.mp4",
                          cuts=[{"id": job_id + "-1", "index": 1, "transcript": "Private words"}])
        return job_id

    def test_unauthenticated_routes_and_headers(self):
        for path in ("/api/jobs", "/api/jobs/missing", "/media/missing/original", "/media/missing/clip-001.mp4", "/api/session"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertIn("includeSubDomains", response.headers["strict-transport-security"])
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertNotIn("server", response.headers)
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "health.internal"}).status_code, 200)
        self.assertEqual(self.client.get("/login", headers={"Host": "evil.test"}).status_code, 400)

    def test_generic_login_and_normalisation(self):
        for email, password in (("unknown@example.test", PASSWORD), ("alice@example.test", "bad"), ("alice@example.test", "wrong-synthetic-password")):
            response = self.login(email, password)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"], LOGIN_ERROR)
        self.assertEqual(self.login(" ALICE@EXAMPLE.TEST ").status_code, 200)

    def test_invalid_password_values_remain_unauthorised(self):
        for password in (None, 123, [], {}, " \t\n", "x" * 129):
            with self.subTest(password=password):
                with self.store.db() as db:
                    db.execute("DELETE FROM limits")
                    user = dict(db.execute("SELECT * FROM users WHERE email=?", ("alice@example.test",)).fetchone())
                response = self.login(password=password)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], LOGIN_ERROR)
                with self.assertRaises(HTTPException) as error:
                    self.auth.reauthenticate(user, password, "test")
                self.assertEqual(error.exception.status_code, 401)

    def test_client_ip_only_trusts_the_loopback_proxy_header(self):
        from security import client_ip
        from starlette.requests import Request
        for peer, header, expected in (("127.0.0.1", "198.51.100.7", "198.51.100.7"),
                                       ("::1", "2001:db8::7", "2001:db8::7"),
                                       ("127.0.0.1", "invalid", "platform-unknown"),
                                       ("198.51.100.8", "198.51.100.7", "198.51.100.8")):
            with self.subTest(peer=peer, header=header):
                request = Request({"type": "http", "client": (peer, 1234),
                                   "headers": [(b"x-splice-client-ip", header.encode()), (b"x-forwarded-for", b"203.0.113.9")]})
                self.assertEqual(client_ip(request, self.settings), expected)

    def test_secure_cookie_rotation_logout_and_hashed_storage(self):
        response = self.login()
        cookie = response.headers["set-cookie"]
        for fragment in ("__Host-splice_session", "Secure", "HttpOnly", "SameSite=lax", "Path=/"):
            self.assertIn(fragment, cookie)
        self.assertNotIn("Domain=", cookie)
        old = self.client.cookies.get(self.settings.cookie_name)
        with self.store.db() as db:
            self.assertNotEqual(db.execute("SELECT digest FROM sessions").fetchone()[0], old)
        self.login()
        self.assertIsNone(self.auth.current(old))
        current = self.client.cookies.get(self.settings.cookie_name)
        response = self.client.post("/api/logout", headers=CSRF)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertIsNone(self.auth.current(current))
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)

    def test_idle_and_absolute_expiry(self):
        for column, duration in (("touched", IDLE_SECONDS), ("created", ABSOLUTE_SECONDS)):
            self.login()
            with self.store.db() as db:
                db.execute(f"UPDATE sessions SET {column}=?", (time.time() - duration - 1,))
            self.assertEqual(self.client.get("/api/jobs").status_code, 401)

    def test_cross_tenant_every_object_even_admin_and_ranges(self):
        job_id = self.fixture_job()
        paths = (f"/api/jobs/{job_id}", f"/media/{job_id}/original", f"/media/{job_id}/clip-001.mp4", f"/api/jobs/{job_id}/transcript/{job_id}-1")
        for email in ("alice@example.test", ADMIN_EMAIL):
            self.login(email)
            self.assertEqual(self.client.get("/api/jobs").json(), {"jobs": []})
            for path in paths:
                self.assertEqual(self.client.get(path).status_code, 404, path)
                self.assertEqual(self.client.get(path, headers={"Range": "bytes=0-3"}).status_code, 404)
            self.assertEqual(self.client.head(paths[1]).status_code, 404)
            self.assertEqual(self.client.put(paths[-1], json={"transcript": "stolen"}, headers=CSRF).status_code, 404)
            self.assertEqual(self.client.delete(paths[0], headers=CSRF).status_code, 404)
        self.login("bob@example.test")
        response = self.client.get(paths[1], headers={"Range": "bytes=0-3"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"synt")
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertIn('filename="splice-original.mp4"', response.headers["content-disposition"])
        self.assertEqual(self.client.put(paths[-1], json={"transcript": "My words"}, headers=CSRF).status_code, 200)
        self.assertEqual(self.client.get(paths[-1]).json()["transcript"], "My words")
        self.assertEqual(self.client.get(f"/media/{job_id}/source.bin").status_code, 404)

    def test_symlink_media_cannot_escape(self):
        job_id = self.fixture_job("alice@example.test")
        directory = self.store.directory(self.users["alice@example.test"], job_id)
        (directory / "source.bin").unlink()
        (directory / "source.bin").symlink_to(self.settings.data / "splice.sqlite3")
        self.login()
        self.assertEqual(self.client.get(f"/media/{job_id}/original").status_code, 404)

    def test_original_url_rebuilt_for_saved_jobs_and_path_deployment(self):
        job_id = self.fixture_job("alice@example.test")
        owner = self.users["alice@example.test"]
        # Existing saved jobs may have no originalUrl, or URLs from a different
        # base path. API presentation must always provide current media routes.
        for base, old_url in (("", None), ("/tools", "/media/old/original")):
            if old_url:
                self.store.update(owner, job_id, originalUrl=old_url)
            config = Settings(self.settings.data, "https://testserver", base, bytes(32), False)
            with TestClient(create_app(config, TestPasswords()), base_url=config.origin) as client:
                client.post(base + "/api/login", headers=CSRF, json={"email": "alice@example.test", "password": PASSWORD})
                result = client.get(base + "/api/jobs/" + job_id).json()
                url = result["originalUrl"]
                self.assertEqual(url, base + f"/media/{job_id}/original")
                self.assertEqual(client.get(base + "/api/jobs").json()["jobs"][0]["originalUrl"], url)
                self.assertEqual(client.get(url).content, b"synthetic-original")
                head = client.head(url)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(int(head.headers["content-length"]), len(b"synthetic-original"))
                tail = client.get(url, headers={"Range": "bytes=10-"})
                self.assertEqual(tail.status_code, 206)
                self.assertEqual(tail.content, b"original")
                self.assertEqual(client.get(result["cuts"][0]["videoUrl"]).content, b"synthetic-video")

    def test_csrf_all_mutations_and_no_get_side_effects(self):
        self.assertEqual(self.client.post("/api/login", json={}).status_code, 403)
        self.login(ADMIN_EMAIL)
        for method, path in (("POST", "/api/analyze-file"), ("POST", "/api/logout"), ("POST", "/api/admin/users"), ("POST", "/api/password"),
                             ("DELETE", "/api/jobs"), ("DELETE", "/api/jobs/x"), ("PUT", "/api/jobs/x/transcript/y")):
            self.assertEqual(self.client.request(method, path).status_code, 403, path)
            self.assertEqual(self.client.request(method, path, headers={**CSRF, "Origin": "https://evil.test"}).status_code, 403, path)
        self.assertEqual(self.client.post("/api/logout", headers={**CSRF, "Sec-Fetch-Site": "cross-site"}).status_code, 403)
        for path in ("/api/login", "/api/logout", "/api/analyze-file"):
            self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.get("/api/session").status_code, 200)

    def test_rate_limits_persist_and_return_retry_after(self):
        for _ in range(5):
            self.assertEqual(self.login(password="wrong").status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["retry-after"]), 0)
        with self.store.db() as db:
            keys = [r[0] for r in db.execute("SELECT bucket FROM limits")]
            self.assertTrue(all("alice" not in key for key in keys))
        # Different emails cannot bypass a shared IP limit.
        with self.store.db() as db:
            db.execute("DELETE FROM limits")
        for index in range(20):
            self.login(f"missing{index}@example.test", "wrong")
        self.assertEqual(self.login("another@example.test").status_code, 429)

    def test_upload_rate_queue_and_cleanup(self):
        self.login()
        for _ in range(5):
            response = self.client.post("/api/analyze-file", files={"file": ("bad.php", b"invalid", "video/mp4")}, headers=CSRF)
            self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/analyze-file", files={"file": ("okay.mp4", b"invalid", "video/mp4")}, headers=CSRF)
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response.headers.get("retry-after"))
        self.assertEqual(list(self.store.uploads.glob("*/*/source.bin")), [])
        with self.store.db() as db:
            db.execute("DELETE FROM limits")
        job = self.store.new_job(self.users["alice@example.test"])
        self.assertRaises(HTTPException, self.store.new_job, self.users["alice@example.test"])
        self.store.new_job(self.users["bob@example.test"])
        self.assertRaises(HTTPException, self.store.new_job, self.users[ADMIN_EMAIL])
        self.store.update(self.users["alice@example.test"], job, status="failed")

    def test_retention_and_restart(self):
        identifiers = [self.fixture_job("alice@example.test") for _ in range(12)]
        self.assertEqual(len(self.store.recent(self.users["alice@example.test"])), 10)
        for job_id in identifiers[:2]:
            self.assertFalse(self.store.directory(self.users["alice@example.test"], job_id).exists())
            with self.assertRaises(HTTPException):
                self.store.job(self.users["alice@example.test"], job_id)
        from storage import Store
        restarted = Store(self.settings.data)
        self.assertEqual(len(restarted.recent(self.users["alice@example.test"])), 10)
        job_id = self.store.new_job(self.users["bob@example.test"])
        restarted.recover()
        self.assertEqual(restarted.job(self.users["bob@example.test"], job_id)["status"], "failed")
        self.assertFalse(restarted.directory(self.users["bob@example.test"], job_id).exists())

    def test_allowlist_admin_reauth_revocation_and_audit(self):
        self.login()
        token = self.client.cookies.get(self.settings.cookie_name)
        self.assertEqual(self.client.get("/admin").status_code, 404)
        self.assertEqual(self.client.get("/api/admin/users").status_code, 404)
        self.login(ADMIN_EMAIL)
        self.assertEqual(self.client.get("/admin").status_code, 200)
        payload = {"email": " ALICE@example.test ", "action": "remove", "password": "wrong"}
        self.assertEqual(self.client.post("/api/admin/users", json=payload, headers=CSRF).status_code, 401)
        payload["password"] = PASSWORD
        self.assertEqual(self.client.post("/api/admin/users", json=payload, headers=CSRF).status_code, 200)
        self.assertIsNone(self.auth.current(token))
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(ADMIN_EMAIL).status_code, 200)
        payload.update(email=ADMIN_EMAIL)
        self.assertEqual(self.client.post("/api/admin/users", json=payload, headers=CSRF).status_code, 400)
        payload.update(email="new@example.test", action="add", initialPassword=PASSWORD)
        self.assertEqual(self.client.post("/api/admin/users", json=payload, headers=CSRF).status_code, 200)
        self.assertEqual(self.login("NEW@example.test").status_code, 200)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 2)

    def test_password_change_reauth_and_revocation(self):
        self.login()
        old = self.client.cookies.get(self.settings.cookie_name)
        response = self.client.post("/api/password", json={"password": PASSWORD, "newPassword": "another-synthetic-password"}, headers=CSRF)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.auth.current(old))
        self.assertEqual(self.login(password="another-synthetic-password").status_code, 200)

    def test_body_size_legacy_routes_and_prefix(self):
        self.login()
        response = self.client.post("/api/password", content=b"x" * 65537, headers=CSRF)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.test/video"}, headers=CSRF).status_code, 404)
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        config = Settings(self.settings.data, "https://testserver", "/tools", bytes(32), True, True)
        with TestClient(create_app(config, TestPasswords()), base_url=config.origin) as client:
            response = client.post("/tools/api/login", json={"email": "alice@example.test", "password": PASSWORD}, headers=CSRF)
            self.assertIn("__Secure-splice_session", response.headers["set-cookie"])
            self.assertIn("Path=/tools", response.headers["set-cookie"])
            self.assertNotIn("__Host-", response.headers["set-cookie"])
            response = client.get("/tools/")
            self.assertEqual(response.status_code, 200)
            self.assertIn('src="/tools/static/app.js"', response.text)
            self.assertIn('data-base-path="/tools"', response.text)
            self.assertEqual(client.get("/tools/api/jobs").status_code, 200)
            self.assertEqual(client.get("/api/jobs").status_code, 401)


class UploadRequest:
    def __init__(self, body, chunk_size=1):
        self.body, self.chunk_size = body, chunk_size
        self.headers = {"content-type": 'multipart/form-data; boundary="ugc-boundary"'}

    async def stream(self):
        for offset in range(0, len(self.body), self.chunk_size):
            yield self.body[offset:offset + self.chunk_size]


def multipart(payload, ending=b"--\r\n"):
    return b'--ugc-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.mp4"\r\nContent-Type: video/mp4\r\n\r\n' + payload + b"\r\n--ugc-boundary" + ending


class UploadTests(unittest.TestCase):
    def test_binary_boundary_splits(self):
        payload = b"\x00\xffsome video\r\n--ugc-not-the-boundary\x00"
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
            for size in (1, 2, 7, 19, 4096):
                path = Path(directory) / f"{size}.upload"
                name = asyncio.run(receive_upload(UploadRequest(multipart(payload), size), path))
                self.assertEqual(name, "test.mp4")
                self.assertEqual(path.read_bytes(), payload)

    def test_names_sizes_and_incomplete_uploads(self):
        for name in (".mp4.php", "video.mkv", "../test.mp4", "foo/bar.mp4", "foo\\bar.mp4", "-i.mp4", "C:video.mp4", "%2e%2e%2fvideo.mp4", "x\x00.mp4"):
            self.assertFalse(valid_filename(name), name)
        for name in ("UPPER.MOV", "video.webm", "$(id).mp4", "; harmless.mp4"):
            self.assertTrue(valid_filename(name), name)
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
            for index, ending in enumerate((b"", b"\r\nContent-Disposition: form-data")):
                with self.assertRaises(HTTPException):
                    asyncio.run(receive_upload(UploadRequest(multipart(b"video", ending)), Path(directory) / str(index)))
            with patch("uploads.MAX_BYTES", 3), self.assertRaises(HTTPException) as error:
                asyncio.run(receive_upload(UploadRequest(multipart(b"four")), Path(directory) / "large"))
            self.assertEqual(error.exception.status_code, 413)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "data")
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.bin"
        self.source.write_bytes(b"\x00\x00\x00\x18ftypisom" + bytes(30))
        self.metadata = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "5"},
                         "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "avg_frame_rate": "30/1"}]}

    def tearDown(self):
        self.temp.cleanup()

    def probe(self):
        return pipeline.probe_video(self.source, self.directory, time.monotonic() + 30, ".mp4")

    def test_probe_rejects_duration_resolution_codec_and_playlist(self):
        with patch("pipeline.run") as run:
            run.return_value = SimpleNamespace(stdout=json.dumps(self.metadata))
            self.assertEqual(self.probe(), (5, False))
            for location, key, value in (("format", "duration", "nan"), ("format", "duration", "301"), ("format", "format_name", "hls"),
                                         ("video", "width", 8192), ("video", "codec_name", "unknown"), ("video", "avg_frame_rate", "120/1")):
                metadata = json.loads(json.dumps(self.metadata))
                (metadata["streams"][0] if location == "video" else metadata["format"])[key] = value
                run.return_value = SimpleNamespace(stdout=json.dumps(metadata))
                with self.assertRaises(pipeline.JobError):
                    self.probe()
        self.source.write_bytes(b"#EXTM3U\nhttps://example.test/secret")
        with self.assertRaises(pipeline.JobError):
            self.probe()

    def test_process_argv_timeout_and_secret_free_environment(self):
        with patch("pipeline.subprocess.run", return_value=SimpleNamespace(stdout="", stderr="")) as run:
            pipeline.run([pipeline.FFMPEG, "-version"], self.directory, time.monotonic() + 10, "Failed")
            kwargs = run.call_args.kwargs
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertLessEqual(kwargs["timeout"], 10)
            self.assertNotIn("SESSION_SECRET", kwargs["env"])
        with patch("pipeline.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            with self.assertRaises(pipeline.JobError):
                pipeline.run([pipeline.FFMPEG], self.directory, time.monotonic() + 10, "Failed")

    def test_scene_flags_and_debounce(self):
        with patch("pipeline.run", return_value=SimpleNamespace(stderr="pts_time:0.01 pts_time:1.25 pts_time:1.26 pts_time:2.5 pts_time:3.74")) as run:
            self.assertEqual(pipeline.cut_boundaries(self.source, 3.75, self.directory, time.monotonic() + 30), [0.0, 1.25, 2.5, 3.75])
            command = run.call_args.args[0]
            self.assertIn("-nostdin", command)
            self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file")
            self.assertIn("-t", command)
            self.assertIn("-fs", command)

    def test_word_timestamps_assigned_once(self):
        cuts = [{"start": 0, "end": 1}, {"start": 1, "end": 2}]
        words = [SimpleNamespace(start=.1, end=.5, word=" Hello"), SimpleNamespace(start=.8, end=1.2, word=" there"), SimpleNamespace(start=1.3, end=1.9, word=" friend.")]
        self.assertEqual(slice_transcript([SimpleNamespace(words=words)], cuts), ["Hello", "there friend."])


if __name__ == "__main__":
    unittest.main()
