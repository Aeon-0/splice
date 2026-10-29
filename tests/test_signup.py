"""Signup/approval boundaries, existing database migration and real Argon2."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app
from config import ADMIN_EMAIL, ROOT, Settings
from security import LOGIN_ERROR
from storage import Store
import test_app

TestPasswords = test_app.TestPasswords
PASSWORD, CSRF = test_app.PASSWORD, test_app.CSRF


class SignupTests(unittest.TestCase):
    setUp = test_app.SecurityTests.setUp
    tearDown = test_app.SecurityTests.tearDown
    login = test_app.SecurityTests.login

    def signup(self, email="new@example.test", password=PASSWORD, **kwargs):
        return self.client.post("/api/signup", json={"email": email, "password": password}, headers=kwargs.pop("headers", CSRF), **kwargs)

    def change(self, action, email="new@example.test", password=PASSWORD):
        return self.client.post("/api/admin/users", headers=CSRF, json={"action": action, "email": email, "password": password})

    def test_pending_has_no_session_or_private_access(self):
        response = self.signup(" NEW@EXAMPLE.TEST ")
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("set-cookie", response.headers)
        for path in ("/api/jobs", "/api/session", "/api/admin/users", "/media/missing/original"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.login("new@example.test").json(), {"detail": LOGIN_ERROR})
        self.assertEqual(self.login("absent@example.test").json(), {"detail": LOGIN_ERROR})
        self.login(ADMIN_EMAIL)
        self.assertIn({"email": "new@example.test", "enabled": 0, "status": "pending"}, self.client.get("/api/admin/users").json()["users"])
        self.assertEqual(self.change("accept", password="incorrect").status_code, 401)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT enabled FROM users WHERE email='new@example.test'").fetchone()[0], 0)
        self.assertEqual(self.change("accept").status_code, 200)
        self.assertEqual(self.login("new@example.test").status_code, 200)
        self.assertEqual(self.client.get("/api/session").json(), {"email": "new@example.test", "admin": False})
        self.assertEqual(self.change("accept", "bob@example.test").status_code, 404)

    def test_duplicate_signup_never_replaces_password_access_or_sessions(self):
        self.signup()
        self.login()
        token = self.client.cookies.get(self.settings.cookie_name)
        with self.store.db() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM users ORDER BY email")]
        for email in ("new@example.test", "alice@example.test", ADMIN_EMAIL):
            response = self.signup(email.upper(), "different-synthetic-password")
            self.assertEqual((response.status_code, response.json()), (202, {"ok": True}))
            self.assertNotIn("set-cookie", response.headers)
        with self.store.db() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM users ORDER BY email")])
        self.assertIsNotNone(self.auth.current(token))

    def test_decline_remove_and_immutable_admin(self):
        self.signup()
        self.login(ADMIN_EMAIL)
        self.assertEqual(self.change("decline", password="incorrect").status_code, 401)
        self.assertEqual(self.change("decline").status_code, 200)
        self.assertEqual(self.change("accept").status_code, 409)
        self.assertEqual(self.signup().status_code, 202)
        self.assertNotIn("new@example.test", [u["email"] for u in self.client.get("/api/admin/users").json()["users"]])
        self.assertEqual(self.login("new@example.test").status_code, 401)
        with self.store.db() as db:
            db.execute("DELETE FROM limits")
        for action in ("accept", "decline", "remove"):
            self.assertEqual(self.change(action, ADMIN_EMAIL).status_code, 400)
        self.assertEqual(self.change("add", "other@example.test").status_code, 400)
        self.assertEqual(self.change("accept", "missing@example.test").status_code, 404)
        with self.store.db() as db:
            db.execute("DELETE FROM limits")
        self.login("alice@example.test")
        token = self.client.cookies.get(self.settings.cookie_name)
        self.login(ADMIN_EMAIL)
        self.assertEqual(self.change("remove", "alice@example.test").status_code, 200)
        self.assertIsNone(self.auth.current(token))
        self.signup("alice@example.test")
        self.assertEqual(self.login("alice@example.test").status_code, 401)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT status FROM users WHERE email='alice@example.test'").fetchone()[0], "removed")
            self.assertEqual([r[0] for r in db.execute("SELECT action FROM audit ORDER BY created")], ["apply", "decline", "remove"])

    def test_signup_validation_csrf_and_persistent_limits(self):
        for headers in ({}, {**CSRF, "Origin": "https://evil.test"}, {**CSRF, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.signup(headers=headers).status_code, 403)
        for email, password in ((None, PASSWORD), ("invalid", PASSWORD), ("x" * 255 + "@example.test", PASSWORD),
                                ("new@example.test", None), ("new@example.test", "short"),
                                ("new@example.test", "passwordpassword"), ("new@example.test", "x" * 129)):
            with self.store.db() as db:
                db.execute("DELETE FROM limits")
            self.assertEqual(self.signup(email, password).status_code, 400)
        with self.store.db() as db:
            db.execute("DELETE FROM limits")
        for _ in range(3):
            self.assertEqual(self.signup().status_code, 202)
        with TestClient(create_app(self.settings, TestPasswords()), base_url=self.settings.origin) as restarted:
            response = restarted.post("/api/signup", headers=CSRF, json={"email": "new@example.test", "password": PASSWORD})
            self.assertEqual(response.status_code, 429)
            self.assertTrue(response.headers.get("retry-after"))
        self.assertEqual(self.signup("second@example.test").status_code, 202)
        self.assertEqual(self.signup("third@example.test").status_code, 202)
        self.assertEqual(self.signup("fourth@example.test").status_code, 429)


class SignupRuntimeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "data").mkdir(exist_ok=True)

    def test_real_argon2_pending_restart_accept_and_prefixed_pages(self):
        for base in ("", "/tools"):
            with self.subTest(base=base), tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
                config = Settings(Path(directory), "https://testserver", base, bytes(32), True)
                application = create_app(config)
                chosen = "synthetic applicant password"
                with patch.dict(os.environ, {"ADMIN_PASSWORD": PASSWORD}), TestClient(application, base_url=config.origin) as client:
                    for path in ("/signup", "/signup/thanks", "/static/signup.js"):
                        page = client.get(base + path)
                        self.assertEqual(page.status_code, 200)
                        self.assertNotIn("__BASE__", page.text)
                    self.assertIn(f'href="{base}/signup"', client.get(base + "/login").text)
                    self.assertIn(f'href="{base}/login"', client.get(base + "/signup").text)
                    self.assertIn("Discord or in coaching chat", client.get(base + "/signup/thanks").text)
                    self.assertEqual(client.post(base + "/api/signup", headers=CSRF,
                                                json={"email": " APPLICANT@example.test ", "password": " " + chosen + "\n"}).status_code, 202)
                    with application.state.store.db() as db:
                        row = db.execute("SELECT * FROM users WHERE email='applicant@example.test'").fetchone()
                        self.assertTrue(row["password_hash"].startswith("$argon2id$"))
                        self.assertTrue(application.state.auth.passwords.verify(row["password_hash"], chosen))
                # Approval uses the stored applicant password after a full restart.
                with TestClient(create_app(config), base_url=config.origin) as client:
                    self.assertEqual(client.post(base + "/api/login", headers=CSRF, json={"email": "applicant@example.test", "password": chosen}).status_code, 401)
                    self.assertEqual(client.post(base + "/api/login", headers=CSRF, json={"email": ADMIN_EMAIL, "password": PASSWORD}).status_code, 200)
                    self.assertEqual(client.post(base + "/api/admin/users", headers=CSRF, json={"email": "applicant@example.test", "action": "accept", "password": PASSWORD}).status_code, 200)
                    self.assertEqual(client.post(base + "/api/login", headers=CSRF, json={"email": "applicant@example.test", "password": chosen}).status_code, 200)

    def test_existing_database_migration_preserves_access_and_passwords(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
            with sqlite3.connect(Path(directory) / "splice.sqlite3") as db:
                db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)")
                for index, enabled in enumerate((1, 0)):
                    db.execute("INSERT INTO users VALUES (?,?,?,?)", (str(index), f"{index}@example.test", "synthetic-hash", enabled))
            for _ in range(2):
                store = Store(Path(directory))
                with store.db() as db:
                    rows = db.execute("SELECT password_hash,enabled,status FROM users ORDER BY id").fetchall()
                    self.assertEqual([tuple(r) for r in rows], [("synthetic-hash", 1, "approved"), ("synthetic-hash", 0, "removed")])
