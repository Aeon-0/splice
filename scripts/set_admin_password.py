"""Offline admin setup: one SQLite path argument; password only from the environment."""
import os
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argon2 import PasswordHasher
from config import ADMIN_EMAIL
from security import MAX_PASSWORD_LENGTH, normalise_password
from storage import migrate_auth_schema


def main():
    # Do not use argparse's unrecognised-argument output: it could echo a secret.
    if len(sys.argv) != 2:
        raise SystemExit("Usage: set_admin_password.py SQLITE_PATH (password via SPLICE_ADMIN_PASSWORD only)")
    password = normalise_password(os.environ.pop("SPLICE_ADMIN_PASSWORD", None))
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        raise SystemExit("SPLICE_ADMIN_PASSWORD must contain 1–1024 characters after surrounding whitespace is stripped.")
    path = Path(sys.argv[1]).resolve()
    # mode=rw refuses a typo or a database that has never been initialised.
    try:
        with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10) as db:
            db.row_factory = sqlite3.Row
            password_hash = PasswordHasher().hash(password)
            db.execute("BEGIN IMMEDIATE")
            migrate_auth_schema(db)
            db.execute("INSERT INTO users (id,email,enabled,status,password_hash) VALUES (?,?,1,'approved',?) "
                       "ON CONFLICT(email) DO UPDATE SET enabled=1,status='approved',password_hash=excluded.password_hash",
                       (str(uuid.uuid4()), ADMIN_EMAIL, password_hash))
            db.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE email=?)", (ADMIN_EMAIL,))
    except (sqlite3.Error, OSError):
        raise SystemExit("Password not set. Check the existing Splice SQLite path, permissions and schema.") from None
    print("Admin password set; existing administrator sessions revoked.")


if __name__ == "__main__":
    main()
