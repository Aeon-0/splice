"""Real Argon2 sign-in, local cookies and account persistence across restarts."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import create_app
from config import ADMIN_EMAIL, ROOT, Settings

# Deliberately public, synthetic test values, never deployment credentials.
PASSWORD = "synthetic-local-test-password"
CSRF = {"X-Requested-With": "Splice"}


class AuthRuntimeTests(unittest.TestCase):
    def test_clipboard_whitespace_with_real_argon2(self):
        password = "synthetic password with spaces"
        (ROOT / "data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
            config = Settings(Path(directory), "https://testserver", "", bytes(32), False)
            application = create_app(config)
            with patch.dict(os.environ, {"ADMIN_PASSWORD": "\t " + password + "\r\n"}):
                with TestClient(application, base_url=config.origin) as client:
                    auth = application.state.auth
                    for padding in ("\n", " \t\r\n", "\u00a0", " " * 140):
                        with self.subTest(padding=repr(padding)):
                            user = auth.authenticate(" " + ADMIN_EMAIL.upper() + " ", padding + password + padding, "test")
                            auth.reauthenticate(user, padding + password + padding, "test")
                    with self.assertRaises(HTTPException) as error:
                        auth.authenticate(ADMIN_EMAIL, password.replace(" ", ""), "test")
                    self.assertEqual(error.exception.status_code, 401)
                    with self.assertRaises(HTTPException) as error:
                        auth.reauthenticate(user, password.replace(" ", ""), "test")
                    self.assertEqual(error.exception.status_code, 401)
                    # Reset consumed test limits before exercising the HTTP boundary.
                    with application.state.store.db() as db:
                        db.execute("DELETE FROM limits")
                    response = client.post("/api/login", headers=CSRF,
                                           json={"email": " " + ADMIN_EMAIL.upper() + " ", "password": password + "\n"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(client.get("/api/session").json(), {"email": ADMIN_EMAIL, "admin": True})
                    changed = "another synthetic password"
                    response = client.post("/api/password", headers=CSRF,
                                           json={"password": "\t" + password + "\n", "newPassword": " " + changed + "\n"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(client.post("/api/login", headers=CSRF,
                                                 json={"email": ADMIN_EMAIL, "password": changed}).status_code, 200)
                    response = client.post("/api/admin/users", headers=CSRF,
                                           json={"email": "viewer@example.test", "action": "add",
                                                 "password": changed + "\n", "initialPassword": " " + password + "\n"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(client.post("/api/login", headers=CSRF,
                                                 json={"email": "viewer@example.test", "password": password}).status_code, 200)

    def test_local_login_recent_restart_and_logout(self):
        (ROOT / "data").mkdir(exist_ok=True)
        for base in ("", "/tools"):
            with self.subTest(base=base), tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
                env = {"SPLICE_ENV": "development", "SESSION_SECRET": "ab" * 32,
                       "SPLICE_DATA_DIR": directory, "SPLICE_BASE_PATH": base,
                       "ADMIN_PASSWORD": PASSWORD}
                with patch.dict(os.environ, env, clear=True):
                    config = Settings.from_env()
                    app = create_app()
                    with TestClient(app, base_url=config.origin) as client:
                        self.assertEqual(client.get(base + "/", follow_redirects=False).headers["location"], base + "/login")
                        page = client.get(base + "/login")
                        self.assertNotIn("__BASE__", page.text)
                        self.assertIn(f'src="{base}/static/login.js"', page.text)
                        response = client.post(base + "/api/login", headers=CSRF,
                                               json={"email": ADMIN_EMAIL.upper(), "password": PASSWORD})
                        self.assertEqual(response.status_code, 200)
                        cookie = response.headers["set-cookie"]
                        self.assertIn("splice_dev_session=", cookie)
                        self.assertNotIn("Secure", cookie)
                        self.assertIn("HttpOnly", cookie)
                        self.assertIn("SameSite=lax", cookie)
                        self.assertEqual(client.get(base + "/api/session").json(), {"email": ADMIN_EMAIL, "admin": True})
                        self.assertNotIn("__BASE__", client.get(base + "/").text)
                        store = app.state.store
                        with store.db() as db:
                            owner = db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0]
                        ids = []
                        for index in range(12):
                            job_id = store.new_job(owner)
                            store.update(owner, job_id, status="complete", originalFilename=f"video-{index}.mp4")
                            ids.append(job_id)
                        expected = ids[-10:][::-1]
                        self.assertEqual([j["jobId"] for j in client.get(base + "/api/jobs").json()["jobs"]], expected)
                        # The allowlist uses the real password provider too.
                        response = client.post(base + "/api/admin/users", headers=CSRF,
                                               json={"email": "viewer@example.test", "action": "add",
                                                     "password": PASSWORD, "initialPassword": PASSWORD})
                        self.assertEqual(response.status_code, 200)
                        saved_cookies = client.cookies

                    # bootstrap consumes the seed. The same account, session and jobs
                    # survive a new application using only SESSION_SECRET and its DB.
                    self.assertNotIn("ADMIN_PASSWORD", os.environ)
                    with TestClient(create_app(), base_url=config.origin, cookies=saved_cookies) as client:
                        self.assertEqual(client.get(base + "/api/session").json()["email"], ADMIN_EMAIL)
                        self.assertEqual([j["jobId"] for j in client.get(base + "/api/jobs").json()["jobs"]], expected)
                        self.assertEqual(client.post(base + "/api/logout", headers=CSRF).status_code, 200)
                        self.assertEqual(client.get(base + "/api/jobs").status_code, 401)
                        self.assertEqual(client.post(base + "/api/login", headers=CSRF,
                                                     json={"email": "viewer@example.test", "password": PASSWORD}).status_code, 200)
                        self.assertEqual(client.get(base + "/api/jobs").json(), {"jobs": []})
                        self.assertEqual(client.get(base + "/api/jobs/" + expected[0]).status_code, 404)

    def test_http_cookie_exception_is_only_for_explicit_loopback_development(self):
        env = {"SESSION_SECRET": "ab" * 32, "SPLICE_ENV": "development"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Settings.from_env().origin, "http://127.0.0.1:8787")
            for origin in ("http://localhost:8787", "http://127.0.0.1:8787", "http://[::1]:8787"):
                with patch.dict(os.environ, {"SPLICE_ORIGIN": origin}):
                    self.assertFalse(Settings.from_env().cookie_secure)
            for mode, origin in (("production", "http://localhost:8787"), ("developmnt", "http://localhost:8787"),
                                 ("development", "http://madebyluke.co"), ("development", "http://127.0.0.1.evil.test")):
                with patch.dict(os.environ, {"SPLICE_ENV": mode, "SPLICE_ORIGIN": origin}), self.assertRaises(RuntimeError):
                    Settings.from_env()
            with patch.dict(os.environ, {"SPLICE_ORIGIN": "https://madebyluke.co", "SPLICE_BASE_PATH": "/tools"}):
                settings = Settings.from_env()
                self.assertTrue(settings.cookie_secure)
                self.assertEqual(settings.cookie_name, "splice_dev_session")


if __name__ == "__main__":
    unittest.main()
