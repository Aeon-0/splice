# Security implementation and release gates

This is the implementation record for [the go-live brief](splice-security-brief.md). Deployment was verified on 29 October 2026 as recorded below; this is **not** certification or a completed production audit. Older release gates remain open unless explicitly verified. Luke's light/blue/product locks supersede the older visual brief. The t1590u email-only requirement supersedes the older password and signup requirements; historical release results below describe their original versions.

## Ranked fixes

| Rank | Implemented control | Verification / outstanding gate |
| --- | --- | --- |
| 1 | Persistent owner-scoped job queries; original/cut/transcript/DELETE/HEAD/ranges check ownership; admin has no media override | Automated cross-tenant and range tests pass |
| 2 | Fixed media binaries, argv, `shell=False`, file-only inputs, no stdin, restricted demuxers, bounded outputs/deadlines; offline killable Whisper child | Command tests and real ffmpeg smoke pass; production native decoder isolation remains a release review item |
| 3 | Host-only Secure/HttpOnly/Lax cookies, HMAC storage, login rotation, logout revocation, idle/absolute expiry | Email-only login/restart and security tests; loopback HTTP development has a separate non-Secure cookie |
| 4 | MP4/MOV/WebM extension, filename, byte and magic checks, restricted ffprobe with duration/resolution/fps/codec gates | Parser/probe tests and actual playlist rejection pass; approved nginx proxy packaged; deployed size cap still requires verification |
| 5 | Generated user/job UUID paths, private volume design, no static media, symlink rejection | Owner/range/path tests pass; volume permissions and mount must be verified on host |
| 6 | Persistent per-IP/account limits, one active job/user, two global slots, one worker; disk-space admission | Rate, queue and Retry-After tests pass; native memory bounds require container tests |
| 7 | Required custom header on every mutation including login, exact Origin check, Fetch Metadata rejection, no permissive CORS | CSRF matrix tests pass; GET mutation routes absent |
| 8 | Strict local-asset CSP, nosniff, frame restrictions, Referrer/Permissions/COOP, no-store and optional HSTS | Application header tests pass; enable HSTS only after HTTPS verification |
| 9 | Session secret validated from platform environment, minimal worker env, no key values in code/config; Fly plan | Existing Fly app deployed; dependency and image audits remain pending |
| 10 | Preferred tools subdomain, `/tools` routes/assets/media/API and matching `__Secure-` cookie Path; hard-bound admin and generic pending responses | Path/cookie/admin tests pass; Luke owns final domain/DNS |

## §3 coverage

- **3.1 Hosting/network:** `fly.toml` describes one Fly machine, private volume, public healthcheck and edge HTTPS. Production is live at `https://madebyluke.co/tools`. Public HTTPS pages and the Fly health check pass; full proxy/client-IP spoof tests, volume ownership and unused-IP review remain outstanding. Railway is intentionally not a second target.
- **3.2 Authentication:** email-only allowlist access, generic pending responses, session rotation/expiry/logout and persistent email/IP/global sign-in limits. Non-approved attempts register pending applicants without a session; declined or removed emails may reapply but require admin approval again. Existing user IDs, media ownership and access decisions survive migration; the password hash column is removed. The t1590u requirement intentionally uses no passwords or email verification and supersedes t1589u.
- **3.3 CSRF:** custom-header approach plus exact supplied Origin and Fetch Metadata checks. No CORS grants. Covers upload, login, logout, access/transcript mutations and deletion.
- **3.4 Objects:** every job query includes owner, all media is routed through auth including Range and HEAD, history limits to ten, admin has no media privilege. Tests cover user A, user B and admin.
- **3.5 Uploads:** stream size, extension, unsafe filename, server path, magic/probe, duration/resolution/fps and codec checks. Retention removes DB rows and directories. Common executable/polyglot signatures are rejected; this is not comprehensive malware classification. The approved nginx byte limit is packaged; container/host verification remains outstanding.
- **3.6 Processes:** argv-only ffmpeg/ffprobe; no stdin; file-only input; no HLS/concat demuxer; fixed model; stripped environment; two decoding threads; bounded output and deadlines; child termination; temp cleanup and orphan sweeper. `showinfo` alone uses info-level logging for scene timestamps. Non-root deployment and VM memory limits require image/host validation. OS-enforced per-job filesystem/network isolation is not yet claimed; an exploit in a native decoder remains a residual risk. Model files and a real spoken-video test remain operator gates.
- **3.7 DoS:** per-account/IP persistent limits, Retry-After, bounded queue and thread worker; uploads time out; disk admission reserves headroom. Proxy request-body cap and native resource load test remain outstanding.
- **3.8 Headers:** application sets CSP, frame restrictions, nosniff, Referrer/Permissions/COOP and no-store; media uses server-generated download labels and explicit video MIME types. HSTS is switchable after HTTPS is proven. No inline script or external font is needed. Inline style property updates only drive progress width; thumbnails use generated data URLs allowed by img-src.
- **3.9 Path:** actual `/tools` route prefixes, asset paths, media URLs, fetches and redirects align with cookie Path. Both cookie-name variants have tests. Apex sibling apps are not treated as isolated by cookie Path.
- **3.10 Supply chain:** runtime credentials are platform-only; Docker context is an allowlist; sensitive/runtime paths are ignored. Exact dependency lock, OSV audit and container/base-OS audit still need completion; the unused Argon2 dependency and adapter are removed. No CI credential was created.
- **3.11 Admin:** hard-bound normalised email, session-authorised Approve/Accept/Decline/Remove, immediate session revocation and audit entries. The admin is immutable and always approved on startup. No automatic impersonation.
- **3.12 Verification:** local unit/UI tests and a real offline ffmpeg smoke pass. Basic hosted HTTPS and health are verified below. Comprehensive TLS, proxy trust/size cap, SSL Labs/Observatory, resource stress, real Whisper and container checks remain outstanding. No local test substitutes for these live results.

Local UX/auth/cut regression run (28 October 2026): **23 Python tests**, **11 UI tests**, JavaScript/shell syntax checks and the offline media smoke pass. Real Argon2 sessions and ten owner-scoped Recent videos survive application restarts at both `/` and `/tools`; original media matches the full source and supports HEAD/Range. The sample now produces nine clips with the missing boundary at 4.667 seconds (expected window 4.60–4.73). The dependency audit could not run because `requirements.lock` is absent; it is not recorded as passing.

Signup regression run (29 October 2026): **32 Python tests** and **16 UI tests** pass, covering the old-database migration, pending access denial, duplicate signup, approval with the original Argon2 password, decline/removal, session revocation, CSRF/rate limits, `/tools`, first-frame thumbnails and the single New Video gate. These are local checks.

Hosted signup release (29 October 2026, commit `871a4a7`): the existing Fly machine is healthy and its old source-file overrides are cleared. SHA-256 hashes of all 12 changed application/auth/UI files match the pushed source. Public login, signup, thank-you, signup script and stylesheet match the release exactly through `madebyluke.co/tools`, with no-store/CSP/nosniff headers. Signup rejects missing CSRF headers and malformed JSON; anonymous job/admin requests return 401. The local tunnel was restarted with its existing session secret and its signup/thank-you pages respond successfully. Approval was tested with synthetic local accounts; no real account password was read or changed. UI coverage uses the DOM harness; a browser layout review was not run.

Email-only regression run (29 October 2026, t1590u): **35 Python tests**, **16 UI tests**, JavaScript/shell syntax checks and the real offline ffmpeg smoke pass. Coverage includes both legacy user schemas, stable owner IDs and Recent videos, generic pending replies, approval after restart, direct approval, decline/reapplication, session revocation and rotation, immutable admin, persistent limits, CSRF, removed password routes and owner-only bulk deletion without re-authentication. Thumbnail decoding, the light/blue interface, original playback and the single New Video gate remain covered. The dependency audit still cannot run because `requirements.lock` is absent; it is not a passing audit.

## Product locks

1. Light only, blue accent; no dark toggle or orange/amber branding.
2. Original is first, New Video last; one Continue confirmation explains that the current video will stop being visible. It remains eligible for the ten-job history rather than being silently destroyed.
3. Recent history is owner-scoped and persisted; overflow is hard-deleted, including media.
4. The immutable administrator is `lukesa100@gmail.com`; approved emails sign in directly; other emails remain pending until approved.
5. Upload-only `.mp4`, `.mov`, `.webm`; legacy URL, local-path and browser-cookie helpers removed; developer startup binds loopback.
6. Fly app `splice-tools` serves `/tools` on the existing machine and private volume; the email-only release removes Argon2 and adds no dependencies.

## Outstanding approval and go-live gates

- **Dependency scope:** nginx remains packaged; t1590u authorises removing unused Argon2. Container and hosted checks below remain outstanding.
- Build/run the non-root container, verify private-volume ownership and request limits, audit pinned runtime and base-OS dependencies, exercise CPU/RAM/time limits and evaluate decoder confinement.
- Provision/cache the offline base model and verify a real spoken video.
- The `/tools` deployment is authorised and live. HSTS remains disabled in the existing configuration; comprehensive TLS and external security checks still require verification.
- ClamAV/CDR, multi-region HA, anonymous access and a separate network-denied worker service remain outside this MVP. No claim of formal ASVS certification is made.
