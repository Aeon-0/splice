"""Deployment settings. Secrets are validated at startup and never logged."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ADMIN_EMAIL = "lukesa100@gmail.com"
MAX_BYTES = 200 * 1024 * 1024
MAX_SECONDS = 300
RETENTION = 10


@dataclass(frozen=True)
class Settings:
    data: Path
    origin: str
    base_path: str
    secret: bytes
    production: bool = True
    hsts: bool = False

    @property
    def cookie_path(self):
        return self.base_path or "/"

    @property
    def cookie_name(self):
        if not self.cookie_secure:
            return "splice_dev_session"
        return "__Secure-splice_session" if self.base_path else "__Host-splice_session"

    @property
    def cookie_secure(self):
        parsed = urlsplit(self.origin)
        return not (not self.production and parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "::1"})

    def url(self, path):
        return self.base_path + path

    @classmethod
    def from_env(cls):
        base = os.environ.get("SPLICE_BASE_PATH", "")
        if base not in ("", "/tools"):
            raise RuntimeError("SPLICE_BASE_PATH must be empty or /tools.")
        production = os.environ.get("SPLICE_ENV", "production") != "development"
        default_origin = "https://tools.madebyluke.co" if production else "http://127.0.0.1:8787"
        origin = os.environ.get("SPLICE_ORIGIN", default_origin).rstrip("/")
        parsed = urlsplit(origin)
        local_http = not production and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if ((parsed.scheme != "https" and not local_http) or not parsed.hostname
                or parsed.path or parsed.query or parsed.fragment or parsed.username is not None):
            raise RuntimeError("SPLICE_ORIGIN must be an HTTPS origin (loopback HTTP is allowed in development), without a path.")
        secret = os.environ.get("SESSION_SECRET", "")
        if not re.fullmatch(r"[a-fA-F0-9]{64,128}", secret):
            raise RuntimeError("Set SESSION_SECRET to at least 32 random bytes encoded as hex via platform secrets.")
        data = Path(os.environ.get("SPLICE_DATA_DIR", str(ROOT / "data"))).resolve()
        if data == ROOT or data.is_relative_to(ROOT / "static"):
            raise RuntimeError("Storage must be separate from the application webroot.")
        if production and data.is_relative_to(ROOT):
            raise RuntimeError("Production requires an external private volume (SPLICE_DATA_DIR=/data).")
        return cls(data, origin, base, bytes.fromhex(secret), production, os.environ.get("SPLICE_HSTS") == "1")
