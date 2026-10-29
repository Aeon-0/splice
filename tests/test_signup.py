"""Email applications, admin decisions and migration from both legacy schemas."""
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app import create_app
from config import ADMIN_EMAIL, ROOT, Settings
from storage import Store
import test_app as fixtures

CSRF = fixtures.CSRF


class AccessTests(unittest.TestCase):
    setUp = fixtures.SecurityTests.setUp
    tearDown = fixtures.SecurityTests.tearDown
    login = fixtures.SecurityTests.login
    fixture_job = fixtures.SecurityTests.fixture_job

    def change(self, action, email="new@example.test", **kwargs):
        return self.client.post("/api/admin/users", headers=kwargs.pop("headers", CSRF),
                                json={"action": action, "email": email}, **kwargs)

    def test_pending_accept_and_direct_approval(self):
        self.assertEqual(self.login(" NEW@EXAMPLE.TEST ").status_code, 202)
        with self.store.db() as db:
            applicant = dict(db.execute("SELECT * FROM users WHERE email='new@example.test'").fetchone())
        self.assertEqual((applicant["enabled"], applicant["status"]), (0, "pending"))
        self.assertEqual(self.change("accept").status_code, 401)
        self.login("bob@example.test")
        self.assertEqual(self.change("accept").status_code, 404)
        self.login(ADMIN_EMAIL)
        listing = self.client.get("/api/admin/users").json()["users"]
        self.assertIn({"email": "new@example.test", "enabled": 0, "status": "pending"}, listing)
        self.assertTrue(all(set(row) == {"email", "enabled", "status"} for row in listing))
        self.assertEqual(self.change("accept").status_code, 200)
        self.assertEqual(self.change("accept").status_code, 409)
        self.assertEqual(self.change("approve", " DIRECT@EXAMPLE.TEST ").status_code, 200)
        self.assertEqual(self.login("new@example.test").status_code, 200)
        self.assertEqual(self.login("direct@example.test").status_code, 200)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT id FROM users WHERE email='new@example.test'").fetchone()[0], applicant["id"])
            self.assertEqual([r[0] for r in db.execute("SELECT action FROM audit ORDER BY created")], ["apply", "accept", "approve"])

    def test_all_unapproved_attempts_share_response_and_can_reapply(self):
        responses = []
        for state in (None, "pending", "declined", "removed"):
            email = f"{state or 'unknown'}@example.test"
            if state:
                with self.store.db() as db:
                    db.execute("INSERT INTO users (id,email,enabled,status) VALUES (?,?,0,?)", (str(uuid.uuid4()), email, state))
            response = self.login(email)
            responses.append((response.status_code, response.json()))
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(self.client.get("/api/session").status_code, 401)
            with self.store.db() as db:
                self.assertEqual(tuple(db.execute("SELECT enabled,status FROM users WHERE email=?", (email,)).fetchone()), (0, "pending"))
        self.assertEqual(responses, [(202, {"ok": True, "pending": True})] * 4)
        # Repeated applications create neither duplicate users nor duplicate audits.
        self.login("unknown@example.test")
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE email='unknown@example.test'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE subject='unknown@example.test'").fetchone()[0], 1)

    def test_decline_remove_reapproval_and_immutable_admin(self):
        self.login("new@example.test")
        self.login(ADMIN_EMAIL)
        self.assertEqual(self.change("decline").status_code, 200)
        self.assertEqual(self.change("accept").status_code, 409)
        for action in ("approve", "accept", "decline", "remove"):
            self.assertEqual(self.change(action, " " + ADMIN_EMAIL.upper() + " ").status_code, 400)
        self.assertEqual(self.change("accept", "missing@example.test").status_code, 404)
        self.assertEqual(self.change("approve", "invalid").status_code, 400)
        self.assertEqual(self.change("unknown").status_code, 400)
        self.assertEqual(self.change("approve", headers={}).status_code, 403)
        job_id = self.fixture_job("alice@example.test")
        self.login("alice@example.test")
        token = self.client.cookies.get(self.settings.cookie_name)
        with TestClient(create_app(self.settings), base_url=self.settings.origin) as admin:
            admin.post("/api/login", headers=CSRF, json={"email": ADMIN_EMAIL})
            self.assertEqual(admin.post("/api/admin/users", headers=CSRF,
                                        json={"action": "remove", "email": "alice@example.test"}).status_code, 200)
            self.assertEqual(self.client.get(f"/media/{job_id}/original").status_code, 401)
            self.assertEqual(self.login("alice@example.test").status_code, 202)
            self.assertEqual(admin.post("/api/admin/users", headers=CSRF,
                                        json={"action": "approve", "email": "alice@example.test"}).status_code, 200)
        self.assertIsNone(self.auth.current(token))
        self.assertEqual(self.login("alice@example.test").status_code, 200)
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").status_code, 200)

    def test_pending_attempt_clears_previous_session(self):
        self.login()
        old = self.client.cookies.get(self.settings.cookie_name)
        response = self.login("pending@example.test")
        self.assertEqual(response.status_code, 202)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertIsNone(self.auth.current(old))
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)

    def test_removal_between_authentication_and_cookie_issue(self):
        user = self.auth.authenticate("alice@example.test", "test")
        with self.store.db() as db:
            db.execute("UPDATE users SET enabled=0,status='removed' WHERE id=?", (user["id"],))
        response = JSONResponse({"ok": True})
        self.assertFalse(self.auth.issue(user, None, response))
        self.assertNotIn("set-cookie", response.headers)

    def test_login_limits_survive_restart(self):
        for _ in range(5):
            self.assertEqual(self.login("new@example.test").status_code, 202)
        with TestClient(create_app(self.settings), base_url=self.settings.origin) as client:
            response = client.post("/api/login", headers=CSRF, json={"email": " NEW@example.test "})
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response.headers["retry-after"]), 0)
        # The service-wide bucket also applies across IPs and addresses.
        with self.store.db() as db:
            db.execute("DELETE FROM limits")
            db.executemany("INSERT INTO limits VALUES (?,?)", [(self.auth.digest("login-global"), time.time())] * 100)
        self.assertEqual(self.login("another@example.test").status_code, 429)


class AccessMigrationTests(unittest.TestCase):
    def test_existing_database_preserves_owners_access_sessions_and_jobs(self):
        (ROOT / "data").mkdir(exist_ok=True)
        for with_status in (False, True):
            with self.subTest(with_status=with_status), tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
                data = Path(directory)
                owner = str(uuid.uuid4())
                with sqlite3.connect(data / "splice.sqlite3") as db:
                    db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1" +
                               (", status TEXT NOT NULL DEFAULT 'approved'" if with_status else "") + ")")
                    for index, state in enumerate(("approved", "removed", "pending", "declined") if with_status else ("approved", "removed")):
                        values = (owner if index == 0 else str(uuid.uuid4()), f"{state}@example.test", "synthetic-legacy-hash", int(state == "approved"))
                        db.execute("INSERT INTO users VALUES (" + ",".join("?" for _ in range(5 if with_status else 4)) + ")",
                                   values + (state,) if with_status else values)
                store = Store(data)
                job_id = store.new_job(owner)
                store.update(owner, job_id, status="complete")
                config = Settings(data, "https://testserver", "/tools", bytes(32), True)
                with TestClient(create_app(config), base_url=config.origin) as client:
                    self.assertEqual(client.post("/tools/api/login", headers=CSRF, json={"email": "approved@example.test"}).status_code, 200)
                    saved = client.cookies
                    self.assertEqual(client.get("/tools/api/jobs").json()["jobs"][0]["jobId"], job_id)
                with TestClient(create_app(config), base_url=config.origin, cookies=saved) as client:
                    self.assertEqual(client.get("/tools/api/session").json()["email"], "approved@example.test")
                    self.assertEqual(client.get("/tools/api/jobs").json()["jobs"][0]["jobId"], job_id)
                    self.assertEqual(client.post("/tools/api/login", headers=CSRF, json={"email": "removed@example.test"}).status_code, 202)
                with store.db() as db:
                    self.assertNotIn("password_hash", {row["name"] for row in db.execute("PRAGMA table_info(users)")})
                    self.assertEqual(db.execute("SELECT id FROM users WHERE email='approved@example.test'").fetchone()[0], owner)
                    if with_status:
                        self.assertEqual(db.execute("SELECT status FROM users WHERE email='declined@example.test'").fetchone()[0], "declined")

    def test_public_pages_and_pending_approval_after_restart(self):
        for base in ("", "/tools"):
            with self.subTest(base=base), tempfile.TemporaryDirectory(dir=ROOT / "data") as directory:
                config = Settings(Path(directory), "https://testserver", base, bytes(32), True)
                for restarted in (False, True):
                    with TestClient(create_app(config), base_url=config.origin) as client:
                        for path in ("/login", "/signup/thanks", "/static/login.js"):
                            response = client.get(base + path)
                            self.assertEqual(response.status_code, 200)
                            self.assertNotIn("__BASE__", response.text)
                            self.assertNotIn("password", response.text.lower())
                        self.assertIn("Discord or in coaching chat", client.get(base + "/signup/thanks").text)
                        self.assertEqual(client.get(base + "/signup", follow_redirects=False).headers["location"], base + "/login")
                        self.assertEqual(client.post(base + "/api/login", headers=CSRF, json={"email": "applicant@example.test"}).status_code, 202)
                        if restarted:
                            self.assertEqual(client.post(base + "/api/login", headers=CSRF, json={"email": ADMIN_EMAIL}).status_code, 200)
                            self.assertEqual(client.post(base + "/api/admin/users", headers=CSRF, json={"email": "applicant@example.test", "action": "accept"}).status_code, 200)
                            self.assertEqual(client.post(base + "/api/login", headers=CSRF, json={"email": "applicant@example.test"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
