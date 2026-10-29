"""Email-only sign-in, local cookies and account persistence across restarts."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app
from config import ADMIN_EMAIL, ROOT, Settings
from test_app import TEST_ADMIN_PASSWORD, seed_admin_password

CSRF = {"X-Requested-With": "Splice"}


class AuthRuntimeTests(unittest.TestCase):
    def test_clipboard_whitespace_and_admin_bootstrap(self):
        (ROOT / "data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
            config = Settings(Path(directory), "https://testserver", "", bytes(32), False)
            application = create_app(config)
            with TestClient(application, base_url=config.origin) as client:
                for padding in ("\n", " \t\r\n", "\u00a0", " " * 140):
                    response = client.post("/api/login", headers=CSRF,
                                           json={"email": padding + ADMIN_EMAIL.upper() + padding})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(client.get("/api/session").json(), {"email": ADMIN_EMAIL, "admin": False})
                with application.state.store.db() as db:
                    owner = db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0]
                    db.execute("UPDATE users SET enabled=0,status='removed' WHERE id=?", (owner,))
            # Startup always restores the immutable admin, preserving its owner ID.
            with TestClient(create_app(config), base_url=config.origin) as client:
                self.assertEqual(client.post("/api/login", headers=CSRF, json={"email": ADMIN_EMAIL}).status_code, 200)
                with application.state.store.db() as db:
                    self.assertEqual(db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0], owner)

    def test_local_login_recent_restart_and_logout(self):
        (ROOT / "data").mkdir(exist_ok=True)
        for base in ("", "/tools"):
            with self.subTest(base=base), tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
                env = {"SPLICE_ENV": "development", "SESSION_SECRET": "ab" * 32,
                       "SPLICE_DATA_DIR": directory, "SPLICE_BASE_PATH": base}
                with patch.dict(os.environ, env, clear=True):
                    config = Settings.from_env()
                    app = create_app()
                    with TestClient(app, base_url=config.origin) as client:
                        self.assertEqual(client.get(base + "/", follow_redirects=False).headers["location"], base + "/login")
                        page = client.get(base + "/login")
                        self.assertNotIn("__BASE__", page.text)
                        self.assertIn(f'src="{base}/static/login.js"', page.text)
                        response = client.post(base + "/api/login", headers=CSRF,
                                               json={"email": ADMIN_EMAIL.upper()})
                        self.assertEqual(response.status_code, 200)
                        cookie = response.headers["set-cookie"]
                        self.assertIn("splice_dev_session=", cookie)
                        self.assertNotIn("Secure", cookie)
                        self.assertIn("HttpOnly", cookie)
                        self.assertIn("SameSite=lax", cookie)
                        self.assertEqual(client.get(base + "/api/session").json(), {"email": ADMIN_EMAIL, "admin": False})
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
                        seed_admin_password(store)
                        self.assertEqual(client.post(base + "/api/admin/login", headers=CSRF,
                                                     json={"email": ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD}).status_code, 200)
                        self.assertEqual(client.post(base + "/api/admin/users", headers=CSRF,
                                                     json={"email": "viewer@example.test", "action": "approve"}).status_code, 200)
                        response = client.post(base + "/api/admin/users", headers=CSRF,
                                               json={"email": "viewer@example.test", "action": "approve"})
                        self.assertEqual(response.status_code, 200)
                        saved_cookies = client.cookies

                    # The same account, session and jobs survive a full restart.
                    with TestClient(create_app(), base_url=config.origin, cookies=saved_cookies) as client:
                        self.assertEqual(client.get(base + "/api/session").json()["email"], ADMIN_EMAIL)
                        self.assertEqual([j["jobId"] for j in client.get(base + "/api/jobs").json()["jobs"]], expected)
                        self.assertEqual(client.post(base + "/api/logout", headers=CSRF).status_code, 200)
                        self.assertEqual(client.get(base + "/api/jobs").status_code, 401)
                        self.assertEqual(client.post(base + "/api/login", headers=CSRF,
                                                     json={"email": "viewer@example.test"}).status_code, 200)
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
