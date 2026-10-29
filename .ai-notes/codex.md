# codex notes: ugc-cuts

Append-only log. One dated entry per job (see the notes rules in AGENTS.md).

## 2026-10-29: Fly code releases
- Mistakes and fixes: Fly [build].dockerfile can override --dockerfile; use the intended Dockerfile in the release context. Check for old Machine file overrides and compare deployed source hashes with the commit.
