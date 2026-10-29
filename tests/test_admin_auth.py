"""Separate admin credentials, safe migrations and env-only offline setup."""
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app import create_app
from config import ADMIN_EMAIL, ROOT, Settings
from storage import Store
import test_app as fixtures

CSRF = fixtures.CSRF
PASSWORD = fixtures.TEST_ADMIN_PASSWORD


class AdminAuthTests(unittest.TestCase):
    setUp = fixtures.SecurityTests.setUp
    tearDown = fixtures.SecurityTests.tearDown
    login = fixtures.SecurityTests.login
    admin_login = fixtures.SecurityTests.admin_login
    fixture_job = fixtures.SecurityTests.fixture_job

    def attempt(self, email=ADMIN_EMAIL, password=PASSWORD, **kwargs):
        return self.client.post("/api/admin/login", headers=kwargs.pop("headers", CSRF),
                                json={"email": email, "password": password}, **kwargs)

    def clear_limits(self):
        with self.store.db() as db:
            db.execute("DELETE FROM limits")

    def test_separate_page_password_success_whitespace_cookie_and_restart(self):
        job_id = self.fixture_job(ADMIN_EMAIL)
        for base in ("", "/tools"):
            with self.subTest(base=base):
                self.clear_limits()
                config = Settings(self.settings.data, "https://testserver", base, bytes(32), True)
                application = create_app(config)
                with TestClient(application, base_url=config.origin) as client:
                    page = client.get(base + "/admin", follow_redirects=False)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('type="password"', page.text)
                    self.assertNotIn('id="approve-form"', page.text)
                    self.assertNotIn("__BASE__", page.text)
                    self.assertEqual(client.get(base + "/static/admin-login.js").status_code, 200)
                    for padding in ("\n", " \t\r\n", "\u00a0", " " * 1100):
                        result = client.post(base + "/api/admin/login", headers=CSRF,
                                             json={"email": " " + ADMIN_EMAIL.upper() + " ", "password": padding + PASSWORD + padding})
                        self.assertEqual(result.status_code, 200)
                        for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=" + (base or "/")):
                            self.assertIn(flag, result.headers["set-cookie"])
                        self.assertEqual(client.get(base + "/api/session").json(), {"email": ADMIN_EMAIL, "admin": True})
                    self.assertIn('id="approve-form"', client.get(base + "/admin").text)
                    self.assertNotIn('type="password"', client.get(base + "/admin").text)
                    self.assertEqual(client.get(base + "/api/admin/users").status_code, 200)
                    self.assertEqual(client.get(base + "/").status_code, 200)
                    self.assertIn('id="admin-link"', client.get(base + "/").text)
                    self.assertEqual(client.get(base + "/api/jobs").json()["jobs"][0]["jobId"], job_id)
                    job = client.get(base + "/api/jobs/" + job_id).json()
                    self.assertEqual(client.get(job["originalUrl"]).content, b"synthetic-original")
                    self.assertEqual(client.get(job["cuts"][0]["videoUrl"]).content, b"synthetic-video")
                    saved = client.cookies
                with TestClient(create_app(config), base_url=config.origin, cookies=saved) as client:
                    self.assertTrue(client.get(base + "/api/session").json()["admin"])
                    self.assertEqual(client.get(base + "/api/admin/users").status_code, 200)
                    self.assertEqual(client.get(base + "/api/jobs").json()["jobs"][0]["jobId"], job_id)

    def test_generic_failure_for_bad_email_password_internal_spaces_and_types(self):
        attempts = [(ADMIN_EMAIL, PASSWORD.replace("  ", " ")), (ADMIN_EMAIL, "wrong"),
                    ("unknown@example.test", PASSWORD), ("alice@example.test", PASSWORD),
                    (None, PASSWORD), ({}, PASSWORD)]
        attempts += [(ADMIN_EMAIL, value) for value in (None, {}, [], 123, " \n", "x" * 1025)]
        for email, password in attempts:
            with self.subTest(email=email, password_type=type(password).__name__):
                self.clear_limits()
                result = self.attempt(email, password)
                self.assertEqual(result.status_code, 401)
                self.assertEqual(result.json(), {"detail": "Invalid email or password"})
                self.assertNotIn("set-cookie", result.headers)
                self.assertEqual(self.client.get("/api/admin/users").status_code, 401)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 3)

    def test_unset_corrupt_or_disabled_hash_never_authenticates(self):
        for hash_value, enabled in ((None, 1), ("broken", 1), (fixtures.TEST_ADMIN_HASH, 0)):
            with self.store.db() as db:
                db.execute("UPDATE users SET password_hash=?,enabled=? WHERE email=?", (hash_value, enabled, ADMIN_EMAIL))
            self.assertEqual(self.client.get("/admin").status_code, 200)
            self.assertEqual(self.attempt().json(), {"detail": "Invalid email or password"})
        # Even a match for the timing-only dummy can never issue an admin session.
        with self.store.db() as db:
            db.execute("UPDATE users SET password_hash=NULL,enabled=1 WHERE email=?", (ADMIN_EMAIL,))
        self.auth.dummy_hash = fixtures.TEST_ADMIN_HASH
        self.assertEqual(self.attempt().status_code, 401)

    def test_admin_email_only_never_creates_a_session_or_pending_application(self):
        for base in ("", "/tools"):
            self.clear_limits()
            config = Settings(self.settings.data, "https://testserver", base, bytes(32), True)
            with TestClient(create_app(config), base_url=config.origin) as client:
                for email in (ADMIN_EMAIL, " \t" + ADMIN_EMAIL.upper() + "\n", "\u00a0" + ADMIN_EMAIL + "\u00a0"):
                    response = client.post(base + "/api/login", headers=CSRF,
                                           json={"email": email, "password": PASSWORD, "admin_authenticated": True, "admin": True})
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.json(), {"ok": True, "pending": True})
                    self.assertNotIn("set-cookie", response.headers)
                    self.assertFalse(client.cookies)
                    for path in ("/api/session", "/api/jobs", "/api/admin/users"):
                        self.assertEqual(client.get(base + path).status_code, 401)
                    self.assertEqual(client.get(base + "/", follow_redirects=False).headers["location"], base + "/login")
                    self.assertIn('id="admin-login-form"', client.get(base + "/admin").text)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)
            self.assertEqual(tuple(db.execute("SELECT enabled,status,password_hash FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()),
                             (1, "approved", fixtures.TEST_ADMIN_HASH))

    def test_admin_email_only_revokes_any_previous_browser_session(self):
        for sign_in in (self.admin_login, self.login):
            self.assertEqual(sign_in().status_code, 200)
            old = self.client.cookies.get(self.settings.cookie_name)
            response = self.login(ADMIN_EMAIL)
            self.assertEqual(response.status_code, 202)
            self.assertIn("Max-Age=0", response.headers["set-cookie"])
            self.assertFalse(self.client.cookies)
            self.assertIsNone(self.auth.current(old))
            self.assertEqual(self.client.get("/api/session").status_code, 401)
            self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.login("alice@example.test").status_code, 200)
        self.assertFalse(self.client.get("/api/session").json()["admin"])
        self.assertNotIn("password", self.client.get("/login").text.lower())

    def test_issue_and_current_reject_email_only_admin_sessions(self):
        with self.store.db() as db:
            user = dict(db.execute("SELECT * FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone())
        response = JSONResponse({"ok": True})
        self.assertFalse(self.auth.issue(user, None, response))
        self.assertNotIn("set-cookie", response.headers)
        # A stale session injected after startup is rejected on the next request.
        token = "A" * 43
        with self.store.db() as db:
            db.execute("INSERT INTO sessions (digest,user_id,created,touched) VALUES (?,?,?,?)",
                       (self.auth.digest(token), user["id"], time.time(), time.time()))
        self.client.cookies.set(self.settings.cookie_name, token)
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_startup_revokes_only_email_only_admin_sessions(self):
        self.admin_login()
        admin_cookie = self.client.cookies.get(self.settings.cookie_name)
        self.client.cookies.clear()
        self.login()
        member_cookie = self.client.cookies.get(self.settings.cookie_name)
        job_id = self.fixture_job(ADMIN_EMAIL)
        with self.store.db() as db:
            db.execute("INSERT INTO sessions (digest,user_id,created,touched) VALUES ('old-email-only',?,?,?)",
                       (self.users[ADMIN_EMAIL], time.time(), time.time()))
        for _ in range(2):
            Store(self.settings.data)
            with self.store.db() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
                self.assertIsNone(db.execute("SELECT 1 FROM sessions WHERE digest='old-email-only'").fetchone())
                self.assertEqual(db.execute("SELECT password_hash FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0], fixtures.TEST_ADMIN_HASH)
                self.assertEqual(db.execute("SELECT owner_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0], self.users[ADMIN_EMAIL])
            self.assertTrue(self.auth.current(admin_cookie)["admin_authenticated"])
            self.assertEqual(self.auth.current(member_cookie)["email"], "alice@example.test")

    def test_admin_account_ip_and_global_limits_persist(self):
        for _ in range(5):
            self.assertEqual(self.attempt(password="wrong").status_code, 401)
        # A correct password and normalised email do not bypass a filled bucket.
        with TestClient(create_app(self.settings), base_url=self.settings.origin) as client:
            response = client.post("/api/admin/login", headers=CSRF,
                                   json={"email": " " + ADMIN_EMAIL.upper() + " ", "password": PASSWORD})
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response.headers["retry-after"]), 0)
        # Admin failures do not lock ordinary email-only sign-in.
        self.assertEqual(self.login().status_code, 200)
        self.clear_limits()
        for index in range(20):
            self.assertEqual(self.attempt(email=f"no-{index}@example.test", password="wrong").status_code, 401)
        self.assertEqual(self.attempt().status_code, 429)
        self.clear_limits()
        with self.store.db() as db:
            db.executemany("INSERT INTO limits VALUES (?,?)", [(self.auth.digest("admin-login-global"), time.time())] * 100)
        self.assertEqual(self.attempt().status_code, 429)

    def test_hash_replacement_between_authentication_and_cookie_issue(self):
        user = self.auth.authenticate_admin(ADMIN_EMAIL, PASSWORD, "synthetic")
        with self.store.db() as db:
            db.execute("UPDATE users SET password_hash=NULL WHERE email=?", (ADMIN_EMAIL,))
        response = JSONResponse({"ok": True})
        self.assertFalse(self.auth.issue(user, None, response, admin_authenticated=True))
        self.assertNotIn("set-cookie", response.headers)

    def test_only_admin_can_store_hash_including_email_changes(self):
        for sql, values in (
            ("UPDATE users SET password_hash=? WHERE email='alice@example.test'", (fixtures.TEST_ADMIN_HASH,)),
            ("INSERT INTO users (id,email,password_hash) VALUES ('invalid','other@example.test',?)", (fixtures.TEST_ADMIN_HASH,)),
            ("UPDATE users SET email='renamed@example.test' WHERE email=?", (ADMIN_EMAIL,)),
        ):
            with self.assertRaises(sqlite3.IntegrityError), self.store.db() as db:
                db.execute(sql, values)

    def test_login_csrf_and_no_public_password_setting(self):
        for headers in ({}, {**CSRF, "Origin": "https://evil.test"}, {**CSRF, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.attempt(headers=headers).status_code, 403)
        self.admin_login()
        for path in ("/api/admin/password", "/api/admin/set-password", "/api/password", "/api/dev/set-password"):
            self.assertEqual(self.client.post(path, headers=CSRF, json={}).status_code, 404)

    def test_env_only_setter_trims_password_preserves_owner_and_revokes_sessions(self):
        self.admin_login()
        old = self.client.cookies.get(self.settings.cookie_name)
        with self.store.db() as db:
            owner = db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()[0]
        script = str(ROOT / "scripts/set_admin_password.py")
        database = str(self.settings.data / "splice.sqlite3")
        result = subprocess.run([sys.executable, script, database], env={"SPLICE_ADMIN_PASSWORD": " \n" + PASSWORD + "\t "},
                                capture_output=True, text=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        with self.store.db() as db:
            row = db.execute("SELECT id,password_hash FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
            self.assertEqual(row["id"], owner)
            self.assertTrue(PasswordHasher().verify(row["password_hash"], PASSWORD))
            self.assertNotIn(row["password_hash"], result.stdout + result.stderr)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE email<>? AND password_hash IS NOT NULL", (ADMIN_EMAIL,)).fetchone()[0], 0)
        self.assertIsNone(self.auth.current(old))
        self.assertEqual(self.admin_login().status_code, 200)
        for args, env in (([database], {}), ([database], {"SPLICE_ADMIN_PASSWORD": " \n"}),
                          ([database, PASSWORD], {}), ([str(self.settings.data / "missing.sqlite3")], {"SPLICE_ADMIN_PASSWORD": PASSWORD})):
            result = subprocess.run([sys.executable, script, *args], env=env, capture_output=True, text=True, cwd=ROOT, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        self.assertFalse((self.settings.data / "missing.sqlite3").exists())


class EmailOnlyMigrationTests(unittest.TestCase):
    def test_legacy_admin_email_only_sessions_are_revoked_and_jobs_survive(self):
        (ROOT / "data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
            data = Path(directory)
            with sqlite3.connect(data / "splice.sqlite3") as db:
                db.execute("CREATE TABLE users (id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'approved')")
                db.execute("CREATE TABLE sessions (digest TEXT PRIMARY KEY,user_id TEXT NOT NULL,created REAL NOT NULL,touched REAL NOT NULL)")
                db.execute("INSERT INTO users (id,email) VALUES ('existing-admin',?)", (ADMIN_EMAIL,))
                db.execute("INSERT INTO sessions VALUES ('existing-session','existing-admin',?,?)", (time.time(), time.time()))
                db.execute("INSERT INTO users (id,email) VALUES ('existing-member','member@example.test')")
                db.execute("INSERT INTO sessions VALUES ('member-session','existing-member',?,?)", (time.time(), time.time()))
                db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)")
                db.execute("INSERT INTO jobs VALUES ('existing-job','existing-admin',0,'complete','{}')")
            for _ in range(2):
                store = Store(data)
                with store.db() as db:
                    self.assertEqual(tuple(db.execute("SELECT id,password_hash FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()), ("existing-admin", None))
                    self.assertEqual([tuple(row) for row in db.execute("SELECT digest,admin_authenticated FROM sessions")], [("member-session", 0)])
                    self.assertEqual(db.execute("SELECT owner_id FROM jobs").fetchone()[0], "existing-admin")
                    self.assertEqual({r["name"]: r["notnull"] for r in db.execute("PRAGMA table_info(users)")}["password_hash"], 0)


if __name__ == "__main__":
    unittest.main()
