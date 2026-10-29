"""Single-machine SQLite persistence; every job query carries its owner."""
import json
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from fastapi import HTTPException

from config import MAX_BYTES, RETENTION


class Store:
    def __init__(self, data):
        self.data = data
        self.uploads = data / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'approved');
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    created REAL NOT NULL, touched REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created REAL NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner_id, created DESC);
                CREATE TABLE IF NOT EXISTS limits (
                    bucket TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS limits_bucket ON limits(bucket, created);
                CREATE TABLE IF NOT EXISTS audit (
                    created REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL);
            """)
            if "status" not in {row["name"] for row in db.execute("PRAGMA table_info(users)")}:
                db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'")
                db.execute("UPDATE users SET status='removed' WHERE enabled=0")

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.data / "splice.sqlite3", timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def directory(self, owner, job_id):
        # Both IDs originate here; still fail closed on corrupt metadata/symlinks.
        for value in (owner, job_id):
            if str(uuid.UUID(value)) != value:
                raise ValueError("Invalid storage identifier")
        path = self.uploads / owner / job_id
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(self.uploads.resolve()):
            raise ValueError("Invalid storage path")
        return path

    def job(self, owner, job_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=? AND owner_id=?", (job_id, owner)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found.")
        return json.loads(row["payload"])

    def recent(self, owner):
        with self.db() as db:
            rows = db.execute("SELECT payload FROM jobs WHERE owner_id=? ORDER BY created DESC LIMIT ?", (owner, RETENTION)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def new_job(self, owner):
        with self.lock, self.db() as db:
            # Reserve room for two bounded jobs plus SQLite/model housekeeping.
            if shutil.disk_usage(self.data).free < 5 * MAX_BYTES:
                raise HTTPException(507, "Storage is temporarily full. Try again later.")
            if db.execute("SELECT 1 FROM jobs WHERE owner_id=? AND status IN ('uploading','queued','processing')", (owner,)).fetchone():
                raise HTTPException(429, "Your previous video is still being prepared.", headers={"Retry-After": "30"})
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('uploading','queued','processing')").fetchone()[0] >= 2:
                raise HTTPException(429, "The worker is busy. Try again shortly.", headers={"Retry-After": "30"})
            job_id = str(uuid.uuid4())
            created = time.time()
            payload = {"jobId": job_id, "createdAt": created, "status": "uploading", "stage": "Receiving your video…",
                       "progress": 0, "cuts": [], "warnings": [], "error": None}
            self.directory(owner, job_id).mkdir(parents=True, mode=0o700)
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (job_id, owner, created, "uploading", json.dumps(payload)))
        self.retain(owner)
        return job_id

    def update(self, owner, job_id, **changes):
        with self.lock:
            payload = self.job(owner, job_id)
            payload.update(changes)
            with self.db() as db:
                db.execute("UPDATE jobs SET status=?, payload=? WHERE id=? AND owner_id=?",
                           (payload["status"], json.dumps(payload), job_id, owner))
        return payload

    def delete(self, owner, job_id):
        with self.lock:
            payload = self.job(owner, job_id)
            if payload["status"] in ("uploading", "queued", "processing"):
                raise HTTPException(409, "Wait for this video to finish before deleting it.")
            directory = self.directory(owner, job_id)
            if directory.exists():
                shutil.rmtree(directory)
            with self.db() as db:
                db.execute("DELETE FROM jobs WHERE id=? AND owner_id=?", (job_id, owner))

    def retain(self, owner):
        with self.lock, self.db() as db:
            stale = db.execute("SELECT id FROM jobs WHERE owner_id=? ORDER BY created DESC LIMIT -1 OFFSET ?", (owner, RETENTION)).fetchall()
            for row in stale:
                self.delete(owner, row["id"])

    def recover(self):
        with self.db() as db:
            interrupted = db.execute("SELECT id, owner_id FROM jobs WHERE status IN ('uploading','queued','processing')").fetchall()
        for row in interrupted:
            self.update(row["owner_id"], row["id"], status="failed", stage="Preparation interrupted", cuts=[], error="Preparation was interrupted. Upload the video again.")
            directory = self.directory(row["owner_id"], row["id"])
            if directory.exists():
                shutil.rmtree(directory)
        self.sweep()

    def sweep(self):
        """Remove orphaned upload directories older than two hours, including after a crash."""
        with self.lock:
            with self.db() as db:
                known = {(r["owner_id"], r["id"]) for r in db.execute("SELECT owner_id,id FROM jobs")}
                db.execute("DELETE FROM limits WHERE created < ?", (time.time() - 3600,))
                db.execute("DELETE FROM sessions WHERE touched < ? OR created < ?", (time.time() - 1800, time.time() - 36000))
            for owner in self.uploads.iterdir():
                if owner.is_symlink() or not owner.is_dir():
                    continue
                for directory in owner.iterdir():
                    if directory.is_symlink() or not directory.is_dir():
                        continue
                    if (owner.name, directory.name) not in known and directory.stat().st_mtime < time.time() - 7200:
                        safe = self.directory(owner.name, directory.name)
                        shutil.rmtree(safe)
