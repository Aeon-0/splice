# Splice

Splice turns an uploaded video into an original player, a strip of cuts and editable transcripts. The interface is light only, with blue branding. Choose a cut to see its video on the left and transcript/duration on the right; smaller screens stack these. **New Video is the last chip** and asks once before clearing the current workspace. Recent videos and Original/Cut cards show first-frame thumbnails. The homepage lists each account's latest **10** jobs; older job records, originals, audio and clips are hard-deleted.

Uploads are **MP4, MOV or WebM only**, up to **200 MiB, 300 seconds, 1920 × 1920 and 60 fps**. There is no URL downloader, Instagram, yt-dlp, local-path API or public media directory. ffmpeg detects hard changes above scene score 0.25, ignoring boundaries within 0.12 seconds of each other. It creates H.264/AAC MP4 cuts; Whisper supplies starting transcripts when the offline model is available. Edits save to the authenticated job, not browser storage.

## Security and access

Read [the security brief](docs/splice-security-brief.md) and [implementation/verification status](docs/security-verification.md) before DNS or creator access.

- Sign in with an email only. Approved emails receive a session immediately; other valid emails are recorded as pending and see the same thank-you page asking them to message Luke on Discord or in coaching chat. Regular users have no password or email-verification step. The separate `/tools/admin` page requires the administrator’s email and password (t1591u). Using the admin email at `/tools/login` grants ordinary app access only.
- Manage access lets the administrator approve an email directly, Accept or Decline pending applicants, and Remove existing access. A password-authenticated admin session and the existing CSRF checks authorise decisions; there is no repeated password prompt for each decision. Removal immediately revokes sessions. A later attempt can register a new pending application but cannot restore access without approval. Decisions are audited. The immutable administrator is `lukesa100@gmail.com`, always approved on startup.
- Existing SQLite users migrate without changing their IDs, access decisions or media ownership. A nullable `password_hash` is restricted to `lukesa100@gmail.com` by database triggers; Argon2id verifies admin passwords only. Existing sessions receive `admin_authenticated=0`. Legacy required password columns are replaced with empty nullable columns; old credentials never restore admin access. There are no public password-setting routes; old `/signup` links redirect to email-only sign-in. Until the offline setup below sets a hash, admin login always fails with `Invalid email or password`. Surrounding password whitespace is stripped; internal spaces remain significant.
- All jobs, originals, cuts, transcript reads/writes, downloads, `HEAD` and byte-range requests check the logged-in owner. Cross-account objects return 404. Private responses use `Cache-Control: no-store`.
- Session identifiers are random, HMAC-hashed in SQLite, rotated on login and revoked on logout. Production cookies are host-only, Secure, HttpOnly and SameSite=Lax; server expiry is 30 minutes idle / 10 hours absolute.
- Mutations—including login—require `X-Requested-With: Splice`; supplied Origin must match the configured origin and cross-site Fetch Metadata is rejected. No cross-origin permissions are enabled. GET does not mutate user data.
- Login limits: 5 attempts/account and 20/IP per 15 minutes, with a global cap of 100. Admin password login has separate persistent buckets with the same limits; an admin lockout does not block ordinary email-only login. Upload/create: 5/hour/account, 10/hour/IP, one active job/account, two active jobs across the service and one processing worker. Limits survive restarts and return 429 with Retry-After.
- Files use generated user/job UUIDs outside the webroot. A bounded streaming parser rejects unsafe filenames and extra form fields. Magic bytes, a restricted ffprobe, codec/duration/resolution/frame-rate limits and common HTML/PHP/playlist signatures are checked before decoding.
- All media commands use argv arrays, `shell=False`, fixed binary paths, no stdin and input protocol `file` only. Playlist demuxers are disabled. Outputs, threads, per-command and total job time are bounded. Decoders inherit a minimal environment without application secrets. Whisper runs in a reusable, killable child and is strictly offline.
- Partial output/audio is removed on failure, timeout or restart; a ten-minute sweep removes orphan directories older than two hours. SQLite and uploads share the private volume. No media has a static-files mount.
- CSP, nosniff, frame restrictions, Referrer-Policy, Permissions-Policy and COOP come from the application. HSTS is enabled after the certificate is verified. Request access logs are disabled; do not enable body, cookie, password or header logging.

## Runtime and local checks

Python 3.12, FastAPI, Uvicorn, faster-whisper and ffmpeg/ffprobe are required. No frontend packages, external fonts, paid transcription or LLM services are used. Dependency additions need Luke's approval under the project rules.

```sh
SPLICE_ENV=development SESSION_SECRET="$(python3 -c 'print("ab" * 32)')" python3 -m unittest discover -s tests -v
node --test tests/test_ui.js
node --check static/app.js
node --check static/login.js
node --check static/admin.js
node --check static/admin-login.js
./scripts/smoke_local.sh
sh -n start.sh stop.sh scripts/smoke_local.sh
python3 scripts/audit_dependencies.py
```

The smoke uses generated videos and synthetic identities in a temporary directory under this project. It opens no listening socket, downloads no model and uses no real secrets. It verifies scene timings/colours, authenticated original/cut range requests, transcript writes, hard deletion and rejected playlists. The auth tests exercise email-only sign-in, pending applications, direct approval, revocation, legacy schema migration, loopback HTTP cookies, account isolation and restart persistence. `tests/test_scene_sample.py` probes the committed sample and verifies the missed 4.667-second Claude transition, nine clips and no tiny fragments, without network or Whisper.

`start.sh` is a developer-only loopback listener at `127.0.0.1:8787`; `stop.sh` checks its process identity. Set `SESSION_SECRET` (64–128 hex characters), then run `./start.sh`. The admin account is created automatically, but admin sign-in remains locked until its password is set using the offline script below. It sets `SPLICE_ENV=development` and defaults to `http://127.0.0.1:8787`; open that exact origin to sign in. Subsequent restarts need only the same session secret and existing database. An optional `SPLICE_ORIGIN` can select another loopback origin or an HTTPS proxy. Only explicit development on loopback HTTP uses a non-Secure `splice_dev_session` cookie; HTTPS and production retain the Secure prefixed cookies. `SPLICE_BASE_PATH=/tools` works locally too. Prefer the in-process smoke for routine verification. Never expose this listener to creators or point production DNS to a Mac, LAN IP, agent box or quick tunnel. Logs and runtime data stay ignored under `data/`.

## Fly.io deployment

**Live at `https://madebyluke.co/tools`**, on the existing Fly app `splice-tools`. The separate admin-login release reuses its single machine and private volume, and restores only `argon2-cffi==23.1.0` and its bindings. Regular user login remains email-only. Release checks and remaining audit limits are recorded in `docs/security-verification.md`.

The original provisioning checklist below uses **`tools.madebyluke.co`** as an alternative hostname. The current configuration uses `/tools`, two CPUs and 4 GiB RAM. Use one machine and one Uvicorn worker: queue capacity and SQLite transactions are intentionally single-machine. Running multiple replicas requires a shared queue/database and distributed limits first. A 30 GiB volume is a starting capacity for a small allowlist; monitor free space and increase it before adding more accounts. Retention is per user, not a global disk quota.

The intended production listener is a size-limiting reverse proxy on port 8080, forwarding only to loopback Uvicorn. The Fly service sets `force_https = true`; the app does not redirect HTTP itself, avoiding redirect loops. Uvicorn proxy-header trust stays disabled. The internal proxy must overwrite client identity and forwarded-protocol headers using platform-supplied values; no direct route to Uvicorn is exposed. The unauthenticated health path is `/api/health` and contains no deployment details.

1. Review the PR, resolve every pending verification gate, and approve hosting costs. Choose an available Fly app name in `fly.toml`; do not assume `splice-tools` is available.
2. Create the Fly app and private volume in `lhr`. Attach the volume as `/data`. Initialise its owner for the non-root container user once, through the platform console. Keep the database/volume off public services. Disable extra machine creation (`fly deploy --ha=false`) and confirm exactly one machine before admitting traffic.
3. Set secrets through Fly's encrypted secret store. Never create a real `.env`, pass secret literals in shell history, paste them into chat, commit them or put them in Dockerfile `ENV`.
4. Deploy to the platform hostname with that HTTPS origin configured. Test health, login, cookies, cross-user media denial, upload bounds, decoding, restart persistence and the volume's permissions before adding custom DNS.
5. Luke adds `tools.madebyluke.co` to Fly, uses Fly's displayed DNS/certificate instructions in Namecheap, and verifies the platform certificate. Do not assume a particular A/AAAA/CNAME value. Release unused dedicated public IPs through the platform after checking their use.
6. Set `SPLICE_ORIGIN=https://tools.madebyluke.co` and `SPLICE_HSTS=1` once HTTPS is stable. Re-run the external checks below before giving creators access. HSTS then sends `max-age=63072000; includeSubDomains`.

### Secrets and configuration

| Setting | Purpose | Where |
| --- | --- | --- |
| `SESSION_SECRET` | At least 32 random bytes, hex encoded (64+ hex characters); HMAC key for session/rate-limit identifiers | Fly secret only |
| `SPLICE_ORIGIN` | Exact public HTTPS origin, no path | `fly.toml` / platform configuration |
| `SPLICE_BASE_PATH` | Empty for subdomain; `/tools` for path deployment | `fly.toml` / platform configuration |
| `SPLICE_DATA_DIR` | Private mounted volume, `/data` | `fly.toml` |
| `SPLICE_HSTS` | `0` during certificate validation; `1` once stable | `fly.toml` |

For Fly, securely pipe secret-manager output containing the required `NAME=value` records into **`fly secrets import --stage`**, then deploy. `fly secrets set` is also supported by the platform, but avoid literal secret arguments in shell history. Generate the session key in a password manager or a trusted secrets workflow; do not print it in logs. Use `fly secrets list` to check names, never values. The admin password is set separately using the one-shot offline procedure below; it is not a persistent application environment variable.

### Set the administrator password (Aeon only)

No password is shipped or set by this release. Run these commands yourself from `/Users/aeon/projects/ugc-cuts` in an interactive terminal. Each prompts without echo; the password is never an argv value or shell-history literal. Use the same password at both prompts if local and Fly should agree. Do not enable shell tracing or pipe debugging logs. Surrounding whitespace is stripped, internal spaces are retained, and the limit is 1024 characters after trimming.

Local (existing `data/splice.sqlite3`):

```sh
python3 -c 'import getpass, os, sys; os.environ["SPLICE_ADMIN_PASSWORD"] = getpass.getpass("Admin password: "); os.execv(sys.executable, [sys.executable, "scripts/set_admin_password.py", "data/splice.sqlite3"])'
```

Fly (existing `/data/splice.sqlite3`; SSH receives the password on stdin with no pseudo-terminal, then passes it to the setter through the environment):

```sh
python3 -c 'import getpass, sys; sys.stdout.write(getpass.getpass("Admin password: "))' | fly ssh console -a splice-tools --user splice --pty=false -C "python -c 'import os,sys; os.environ[\"SPLICE_ADMIN_PASSWORD\"]=sys.stdin.read(); os.execv(sys.executable,[sys.executable,\"/app/scripts/set_admin_password.py\",\"/data/splice.sqlite3\"])'"
```

The script accepts exactly one argument, the SQLite path, and reads the password only from `SPLICE_ADMIN_PASSWORD`. It refuses missing/blank values and non-existent databases, runs the auth migration transactionally, preserves the admin owner ID and revokes existing sessions for that account. It prints neither the password nor its hash. Ordinary users and their media are unchanged. No server restart is needed after setting or changing the password. Then sign in at `/tools/admin` (or `/admin` when the local base path is empty). See [Fly SSH console options](https://fly.io/docs/flyctl/ssh-console/) and [Argon2’s API](https://argon2-cffi.readthedocs.io/en/stable/api.html).

Rotating `SESSION_SECRET` invalidates all session identifiers. Rotate it after a leak and revoke sessions; inspect exposure before restoring access. There is no database URL or third-party API key in this SQLite/local-inference deployment. Any future secret must go through the same platform vault. Railway's equivalent is a **sealed variable**, but no Railway deployment is configured by this PR.

For a future CI deployment, use an app-scoped deployment token stored in CI secrets, never a personal token. This change does not enable automatic deploys or create a token.

### Optional `/tools` hosting

The subdomain is preferred. If Luke chooses `https://madebyluke.co/tools` instead:

- Set `SPLICE_ORIGIN=https://madebyluke.co` and **`SPLICE_BASE_PATH=/tools`**. This is the app's basePath: actual routes, media URLs, assets, login redirects and fetches all carry `/tools`.
- Preserve that prefix through the trusted reverse proxy; do not strip it. Update the healthcheck to `/tools/api/health`.
- The cookie becomes **`__Secure-splice_session; Path=/tools`**. On the subdomain it is **`__Host-splice_session; Path=/`**. Neither sets Domain. There is no CSRF cookie.
- Cookie Path is not an XSS boundary. Keep every sibling app on the apex trusted; the subdomain offers a cleaner boundary. A second proxy must also preserve trustworthy platform client identity for rate limits; otherwise use a stricter shared-IP limit.

### Offline Whisper and resource limits

Provide the `base` faster-whisper model files under `/data/models/base` before enabling transcription. Fetch public model files in an explicit build/provisioning step, without a token, and verify their provenance. The running app never downloads models or contacts a transcription service. Without the model, cuts still complete and users can type their words. Verify a real spoken clip before release; a silent synthetic video cannot establish transcription accuracy.

There is one reused model process and one ffmpeg pipeline. Each job has a 300-second overall deadline, ffprobe 20 seconds, individual ffmpeg operations at most 120 seconds and Whisper at most 180 seconds within that deadline. The Fly machine memory limit is the final bound on native decoders. Output clips plus source have a 400 MiB job budget; temporary speech is capped separately and deleted. Extremely complex videos can fail safely within these limits. No OS decoder sandbox or malware scanner is claimed; restricted input formats/protocols, private paths, non-root execution and current builds are required. Network-denied worker isolation and ClamAV are later hardening items.

## Before public DNS / creator access

Run every automated check, build and test the actual container, audit pinned dependencies and OS packages, and exercise real model inference. On the deployed platform hostname, verify TLS 1.2/1.3, request-size rejection at the proxy, spoofed-forward-header behaviour, persistent owner isolation across a restart, memory/time limits and storage cleanup. After Luke configures DNS, verify custom-domain TLS, both cookie flags and paths, HSTS and the remaining headers with `curl -I`; run SSL Labs and Mozilla Observatory. Check that no production route or service connects to a Mac or quick tunnel. Record observed results in `docs/security-verification.md`; do not mark these operator checks as passing from local tests.

Authoritative deployment references: [Fly app configuration](https://fly.io/docs/reference/configuration/), [Fly request headers](https://fly.io/docs/networking/request-headers/) and [Fly secrets](https://fly.io/docs/apps/secrets/). Fly documents the source port as overwritten by its proxy and warns about trusting forwarded address chains; the application therefore avoids generic proxy-header trust.
