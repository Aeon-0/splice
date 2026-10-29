#!/bin/sh
# Read-only billing capability check. Fly currently exposes no actual MTD cost
# through flyctl/GraphQL. Exit 2 means unavailable, NEVER a zero-dollar bill.
# See README.md, "Reading the current bill". Credentials are never saved or printed.
set -eu
exec python3 - "$@" <<'PY'
import datetime
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def fail(message, status=1):
    print("fly_cost: " + message, file=sys.stderr)
    raise SystemExit(status)


if len(sys.argv) > 2 or (len(sys.argv) == 2 and not re.fullmatch(r"[a-z0-9-]+", sys.argv[1])):
    fail("usage: scripts/fly_cost.sh [org-slug] (default: personal)")
org = sys.argv[1] if len(sys.argv) == 2 else "personal"
fly = shutil.which("fly") or shutil.which("flyctl")
if not fly:
    fail("flyctl is required; use an existing authenticated installation.")

try:
    auth = subprocess.run([fly, "auth", "token", "--quiet"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=20)
except (OSError, subprocess.TimeoutExpired):
    fail("could not obtain the existing flyctl authentication token.")
if auth.returncode or not auth.stdout.strip():
    fail("flyctl authentication is unavailable; sign in with fly auth login.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward the Authorization header to another URL.


query = """query($slug: String!) {
  organization(slug: $slug) { slug }
  __type(name: "Organization") { fields(includeDeprecated: true) { name } }
}"""
request = urllib.request.Request(
    "https://api.fly.io/graphql",
    data=json.dumps({"query": query, "variables": {"slug": org}}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + auth.stdout.strip()},
)
try:
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        result = json.load(response)
except urllib.error.HTTPError as error:
    fail(f"Fly GraphQL returned HTTP {error.code}; cost is unknown.")
except (OSError, ValueError):
    fail("Fly GraphQL could not be read; cost is unknown.")
if not isinstance(result, dict) or result.get("errors"):
    fail("Fly GraphQL rejected the read-only query; cost is unknown.")
data = result.get("data") or {}
if not data.get("organization") or not data.get("__type"):
    fail("organisation or billing schema is unavailable; cost is unknown.")

print("Organisation:", org)
print("Month (UTC):", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m"))
print("Month-to-date cost (USD): unavailable")
print("Per-day cost (USD): unavailable")
print("Billing dashboard: https://fly.io/dashboard/" + org + "/billing")
fields = {field["name"] for field in data["__type"]["fields"]}
if "billables" not in fields:
    fail("authenticated successfully, but Fly's GraphQL billables field is absent. "
         "flyctl has no billing command. Credit balance is not spend. "
         "The dashboard requires browser sign-in; no reliable token-only cost lookup is available.", 2)
fail("Fly's billing schema has changed; verify its amount, currency and period before reporting cost.", 2)
PY
