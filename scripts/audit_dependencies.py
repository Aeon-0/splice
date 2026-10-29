"""Audit pinned runtime versions against the public OSV database, without installing tools."""
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    lock = Path(__file__).resolve().parents[1] / "requirements.lock"
    if not lock.exists():
        raise SystemExit("No runtime lockfile exists yet.")
    packages = []
    for line in lock.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, version = line.split("==")
        packages.append((name, version))
    request = Request("https://api.osv.dev/v1/querybatch", data=json.dumps({"queries": [
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": version} for name, version in packages
    ]}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            results = json.load(response)["results"]
    except (URLError, KeyError, ValueError):
        raise SystemExit("Dependency audit unavailable; do not treat this as a passing audit.") from None
    failed = False
    for (name, version), result in zip(packages, results, strict=True):
        for vulnerability in result.get("vulns", []):
            print(f"{name} {version}: {vulnerability['id']}")
            failed = True
    if failed:
        raise SystemExit("Resolve the reported advisories before deployment.")
    print(f"PASS: OSV reported no known advisories for {len(packages)} pinned Python packages.")


if __name__ == "__main__":
    main()
