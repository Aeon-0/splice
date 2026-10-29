"""Host-only sessions, allowlist authorisation, persistent limits and CSRF checks."""
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from config import ADMIN_EMAIL, MAX_BYTES

IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 36000
MAX_PASSWORD_LENGTH = 1024
HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
    "cache-control": "no-store",
}


def normalise_email(value):
    return value.strip().lower() if isinstance(value, str) else ""


def valid_email(value):
    return len(value) <= 254 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is not None


def normalise_password(value):
    return value.strip() if isinstance(value, str) else ""


def is_admin(user):
    return bool(user and user["email"] == ADMIN_EMAIL and user.get("admin_authenticated") and user.get("password_hash"))


class Auth:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.hasher = PasswordHasher()
        # Timing-only hash, never assigned to an account or accepted for login.
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def digest(self, value):
        return hmac.new(self.settings.secret, value.encode(), hashlib.sha256).hexdigest()

    def bootstrap(self):
        with self.store.db() as db:
            db.execute("INSERT INTO users (id,email,enabled,status) VALUES (?,?,1,'approved') "
                       "ON CONFLICT(email) DO UPDATE SET enabled=1,status='approved'",
                       (str(uuid.uuid4()), ADMIN_EMAIL))

    def limit(self, *buckets):
        """Consume limits atomically; keys are HMACs, never raw email or IP."""
        now = time.time()
        with self.store.db() as db:
            db.execute("DELETE FROM limits WHERE created < ?", (now - 3600,))
            if db.execute("SELECT COUNT(*) FROM limits").fetchone()[0] > 20000:
                raise HTTPException(429, "Too many requests. Try again later.", headers={"Retry-After": "900"})
            for key, maximum, window in buckets:
                rows = db.execute("SELECT created FROM limits WHERE bucket=? AND created>? ORDER BY created", (self.digest(key), now - window)).fetchall()
                if len(rows) >= maximum:
                    retry = max(1, int(rows[0]["created"] + window - now) + 1)
                    raise HTTPException(429, "Too many requests. Try again later.", headers={"Retry-After": str(retry)})
            for key, _, _ in buckets:
                db.execute("INSERT INTO limits VALUES (?,?)", (self.digest(key), now))

    def authenticate(self, email, ip):
        email = normalise_email(email)
        self.limit(("login-ip:" + ip, 20, 900), ("login-account:" + email[:254], 5, 900), ("login-global", 100, 900))
        if not valid_email(email):
            raise HTTPException(400, "Enter a valid email address.")
        if email == ADMIN_EMAIL:
            # Only the password endpoint may authenticate the administrator.
            return None
        with self.store.db() as db:
            # Serialise the access decision and pending registration with admin
            # changes, including callers using a separate Store connection.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row and row["enabled"] and row["status"] == "approved":
                return dict(row)
            if row is None:
                db.execute("INSERT INTO users (id,email,enabled,status) VALUES (?,?,0,'pending')",
                           (str(uuid.uuid4()), email))
            else:
                db.execute("UPDATE users SET enabled=0,status='pending' WHERE id=?", (row["id"],))
            if row is None or row["status"] != "pending":
                self.audit(db, email, "apply", email)
        return None

    def authenticate_admin(self, email, password, ip):
        email = normalise_email(email)
        password = normalise_password(password)
        self.limit(("admin-login-ip:" + ip, 20, 900), ("admin-login-account:" + email[:254], 5, 900),
                   ("admin-login-global", 100, 900))
        with self.store.db() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
        configured = bool(row and row["password_hash"])
        valid = False
        if password and len(password) <= MAX_PASSWORD_LENGTH:
            try:
                valid = self.hasher.verify(row["password_hash"] if configured else self.dummy_hash, password)
            except (InvalidHashError, VerificationError):
                pass
        if not (valid and configured and email == ADMIN_EMAIL and row["enabled"] and row["status"] == "approved"):
            raise HTTPException(401, "Invalid email or password")
        return dict(row)

    def current(self, token):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        digest = self.digest(token)
        now = time.time()
        with self.store.db() as db:
            row = db.execute("SELECT users.*, sessions.created, sessions.touched, sessions.admin_authenticated FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.digest=?", (digest,)).fetchone()
            if row is None:
                return None
            if (not row["enabled"] or row["status"] != "approved"
                    or (row["email"] == ADMIN_EMAIL and (not row["admin_authenticated"] or not row["password_hash"]))
                    or now - row["touched"] >= IDLE_SECONDS or now - row["created"] >= ABSOLUTE_SECONDS):
                db.execute("DELETE FROM sessions WHERE digest=?", (digest,))
                return None
            db.execute("UPDATE sessions SET touched=? WHERE digest=?", (now, digest))
        return dict(row)

    def issue(self, user, old, response, *, admin_authenticated=False):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT email,enabled,status,password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if active is None or not active["enabled"] or active["status"] != "approved":
                return False
            if active["email"] == ADMIN_EMAIL and not admin_authenticated:
                return False
            if admin_authenticated and (active["email"] != ADMIN_EMAIL or not active["password_hash"]
                                        or active["password_hash"] != user.get("password_hash")):
                # A concurrent password reset must also defeat an in-flight login.
                return False
            if old:
                db.execute("DELETE FROM sessions WHERE digest=?", (self.digest(old),))
            db.execute("INSERT INTO sessions (digest,user_id,created,touched,admin_authenticated) VALUES (?,?,?,?,?)",
                       (self.digest(token), user["id"], now, now, int(admin_authenticated)))
        response.set_cookie(self.settings.cookie_name, token, max_age=ABSOLUTE_SECONDS,
                            path=self.settings.cookie_path, secure=self.settings.cookie_secure, httponly=True, samesite="lax")
        return True

    def logout(self, token, response):
        if token:
            with self.store.db() as db:
                db.execute("DELETE FROM sessions WHERE digest=?", (self.digest(token),))
        response.delete_cookie(self.settings.cookie_name, path=self.settings.cookie_path,
                               secure=self.settings.cookie_secure, httponly=True, samesite="lax")

    def audit(self, db, actor, action, subject):
        db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), actor, action, subject))


def client_ip(request, settings):
    peer = request.client.host if request.client else "unknown"
    # Only the loopback nginx instance may pass a Fly-derived identity. Ignore all
    # forwarding headers in local/test mode and all X-Forwarded-For headers.
    if settings.production and peer in ("127.0.0.1", "::1"):
        candidate = request.headers.get("x-splice-client-ip", "")
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return "platform-unknown"
    return peer


class SecurityMiddleware:
    def __init__(self, app, state):
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = self.state.settings
        request = Request(scope)
        path = scope["path"]
        relative = "/" if settings.base_path and path == settings.base_path else path[len(settings.base_path):] if path.startswith(settings.base_path + "/") else path
        public = relative in ("/api/health", "/login", "/api/login", "/signup", "/signup/thanks",
                              "/admin", "/api/admin/login", "/static/admin-login.js",
                              "/static/styles.css", "/static/login.js", "/static/favicon.svg")

        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = {k.lower(): v for k, v in message.get("headers", []) if k.lower() not in (b"server", b"x-powered-by")}
                for key, value in HEADERS.items():
                    headers[key.encode()] = value.encode()
                if settings.hsts:
                    headers[b"strict-transport-security"] = b"max-age=63072000; includeSubDomains"
                # Preserve multiple Set-Cookie headers if the route ever emits them.
                cookies = [(k, v) for k, v in message.get("headers", []) if k.lower() == b"set-cookie"]
                headers.pop(b"set-cookie", None)
                message["headers"] = list(headers.items()) + cookies
            await send(message)

        try:
            host = request.headers.get("host", "")
            from urllib.parse import urlsplit
            if relative != "/api/health" and host != urlsplit(settings.origin).netloc:
                raise HTTPException(400, "Invalid host.")
            user = None if relative == "/api/health" else self.state.auth.current(request.cookies.get(settings.cookie_name))
            scope.setdefault("state", {})["user"] = user
            scope["state"]["ip"] = client_ip(request, settings)
            if not public and user is None:
                if relative in ("/", "/admin"):
                    return await RedirectResponse(settings.url("/login"), status_code=303)(scope, receive, secured_send)
                raise HTTPException(401, "Please sign in.")
            if relative.startswith("/api/admin") and relative != "/api/admin/login":
                if not is_admin(user):
                    raise HTTPException(404, "Not found.")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if (request.headers.get("x-requested-with") != "Splice"
                        or request.headers.get("sec-fetch-site") == "cross-site"
                        or request.headers.get("origin", settings.origin) != settings.origin):
                    raise HTTPException(403, "The request could not be verified.")
            limit = MAX_BYTES + 16384 if relative == "/api/analyze-file" else 65536
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise HTTPException(413, "Request too large.")
            size = 0

            async def limited_receive():
                nonlocal size
                message = await receive()
                if message["type"] == "http.request":
                    size += len(message.get("body", b""))
                    if size > limit:
                        raise HTTPException(413, "Request too large.")
                return message

            await self.app(scope, limited_receive, secured_send)
        except HTTPException as error:
            if not started:
                await JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)(scope, receive, secured_send)
        except Exception:
            # Never expose raw parser, filesystem or decoder errors.
            if not started:
                await JSONResponse({"detail": "The request could not be completed."}, status_code=500)(scope, receive, secured_send)
