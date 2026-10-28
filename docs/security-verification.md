# Security implementation and release gates

This is the implementation record for [the go-live brief](splice-security-brief.md). It does **not** claim deployment, certification, a completed production audit or public DNS. Updated 28 October 2026. Luke's light/blue/product locks supersede the older visual brief.

## Ranked fixes

| Rank | Implemented control | Verification / outstanding gate |
| --- | --- | --- |
| 1 | Persistent owner-scoped job queries; original/cut/transcript/DELETE/HEAD/ranges check ownership; admin has no media override | Automated cross-tenant and range tests pass |
| 2 | Fixed media binaries, argv, `shell=False`, file-only inputs, no stdin, restricted demuxers, bounded outputs/deadlines; offline killable Whisper child | Command tests and real ffmpeg smoke pass; production native decoder isolation remains a release review item |
| 3 | Host-only Secure/HttpOnly/Lax cookies, HMAC storage, login rotation, logout revocation, idle/absolute expiry | Real Argon2 login/restart tests and injected-hasher security tests; loopback HTTP development has a separate non-Secure cookie |
| 4 | MP4/MOV/WebM extension, filename, byte and magic checks, restricted ffprobe with duration/resolution/fps/codec gates | Parser/probe tests and actual playlist rejection pass; approved nginx proxy packaged; deployed size cap still requires verification |
| 5 | Generated user/job UUID paths, private volume design, no static media, symlink rejection | Owner/range/path tests pass; volume permissions and mount must be verified on host |
| 6 | Persistent per-IP/account limits, one active job/user, two global slots, one worker; disk-space admission | Rate, queue and Retry-After tests pass; native memory bounds require container tests |
| 7 | Required custom header on every mutation including login, exact Origin check, Fetch Metadata rejection, no permissive CORS | CSRF matrix tests pass; GET mutation routes absent |
| 8 | Strict local-asset CSP, nosniff, frame restrictions, Referrer/Permissions/COOP, no-store and optional HSTS | Application header tests pass; enable HSTS only after HTTPS verification |
| 9 | Secrets validated from platform environment, bootstrap removal from process environment, minimal worker env, no key values in code/config; Fly plan | Production dependency/container packaging and image audit pending; no paid resource provisioned |
| 10 | Preferred tools subdomain, `/tools` routes/assets/media/API and matching `__Secure-` cookie Path; hard-bound admin and generic login errors | Path/cookie/admin tests pass; Luke owns final domain/DNS |

## §3 coverage

- **3.1 Hosting/network:** `fly.toml` describes one Fly machine, private volume, public healthcheck and edge HTTPS. No production traffic has moved. Proxy packaging, certificate checks, non-root volume ownership, client-IP spoof tests and unused-IP review remain before release. Railway is intentionally not a second target.
- **3.2 Authentication:** allowlist, generic errors, session rotation/expiry/logout, 15–128 character policy and re-authentication on sensitive operations are implemented. The approved Argon2id adapter is installed and exercised by local sign-in/restart tests. No open signup or shared-user password is present.
- **3.3 CSRF:** custom-header approach plus exact supplied Origin and Fetch Metadata checks. No CORS grants. Covers upload, login, logout, password/allowlist/transcript mutations and deletion.
- **3.4 Objects:** every job query includes owner, all media is routed through auth including Range and HEAD, history limits to ten, admin has no media privilege. Tests cover user A, user B and admin.
- **3.5 Uploads:** stream size, extension, unsafe filename, server path, magic/probe, duration/resolution/fps and codec checks. Retention removes DB rows and directories. Common executable/polyglot signatures are rejected; this is not comprehensive malware classification. The approved nginx byte limit is packaged; container/host verification remains outstanding.
- **3.6 Processes:** argv-only ffmpeg/ffprobe; no stdin; file-only input; no HLS/concat demuxer; fixed model; stripped environment; two decoding threads; bounded output and deadlines; child termination; temp cleanup and orphan sweeper. `showinfo` alone uses info-level logging for scene timestamps. Non-root deployment and VM memory limits require image/host validation. OS-enforced per-job filesystem/network isolation is not yet claimed; an exploit in a native decoder remains a residual risk. Model files and a real spoken-video test remain operator gates.
- **3.7 DoS:** per-account/IP persistent limits, Retry-After, bounded queue and thread worker; uploads time out; disk admission reserves headroom. Proxy request-body cap and native resource load test remain outstanding.
- **3.8 Headers:** application sets CSP, frame restrictions, nosniff, Referrer/Permissions/COOP and no-store; media uses server-generated download labels and explicit video MIME types. HSTS is switchable after HTTPS is proven. No inline script or external font is needed. Inline style property updates only drive progress width; thumbnails use generated data URLs allowed by img-src.
- **3.9 Path:** actual `/tools` route prefixes, asset paths, media URLs, fetches and redirects align with cookie Path. Both cookie-name variants have tests. Apex sibling apps are not treated as isolated by cookie Path.
- **3.10 Supply chain:** runtime credentials are platform-only; Docker context is an allowlist; sensitive/runtime paths are ignored. Exact dependency lock, OSV audit and container/base-OS audit still need completion; the approved Argon2 package is present. No CI credential was created.
- **3.11 Admin:** hard-bound normalised email, re-authenticated add/remove/reset, session revocation, immutable audit entries. Password hashes never appear in the list response. No automatic impersonation.
- **3.12 Verification:** local unit/UI tests and a real offline ffmpeg smoke pass. Hosted TLS, DNS, proxy trust/size cap, SSL Labs/Observatory, resource stress, real Whisper and container checks are outstanding. No local test substitutes for these live results.

Local UX/auth/cut regression run (28 October 2026): **23 Python tests**, **11 UI tests**, JavaScript/shell syntax checks and the offline media smoke pass. Real Argon2 sessions and ten owner-scoped Recent videos survive application restarts at both `/` and `/tools`; original media matches the full source and supports HEAD/Range. The sample now produces nine clips with the missing boundary at 4.667 seconds (expected window 4.60–4.73). The dependency audit could not run because `requirements.lock` is absent; it is not recorded as passing.

## Product locks

1. Light only, blue accent; no dark toggle or orange/amber branding.
2. Original is first, New Video last; one Continue confirmation explains that the current video will stop being visible. It remains eligible for the ten-job history rather than being silently destroyed.
3. Recent history is owner-scoped and persisted; overflow is hard-deleted, including media.
4. Allowlist admin is `lukesa100@gmail.com`; secrets and initial-password operation are documented.
5. Upload-only `.mp4`, `.mov`, `.webm`; legacy URL, local-path and browser-cookie helpers removed; developer startup binds loopback.
6. Fly edge-TLS/private-volume configuration drafted; no deployment, DNS or paid provisioning has been performed.

## Outstanding approval and go-live gates

- **Dependency approval received:** Luke approved argon2-cffi and nginx; both are packaged. Container and hosted checks below remain outstanding.
- Build/run the non-root container, verify private-volume ownership and request limits, audit pinned runtime and base-OS dependencies, exercise CPU/RAM/time limits and evaluate decoder confinement.
- Provision/cache the offline base model and verify a real spoken video.
- Luke approves hosting expenditure and operates the deployment. Verify everything on the platform HTTPS hostname first, then configure Namecheap DNS for `tools.madebyluke.co`, verify the custom-domain certificate, enable HSTS and run the external checks.
- ClamAV/CDR, multi-region HA, anonymous access and a separate network-denied worker service remain outside this MVP. No claim of formal ASVS certification is made.
