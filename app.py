"""Splice: authenticated, upload-only cut sheets on one private-volume machine."""
import asyncio
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from config import ADMIN_EMAIL, ROOT, Settings
from pipeline import WhisperWorker, process_job
from security import Auth, SecurityMiddleware, normalise_email, normalise_password, valid_email, valid_password
from storage import Store
from uploads import receive_upload


async def body_json(request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid request.") from None


def create_app(settings=None, passwords=None):
    @asynccontextmanager
    async def lifespan(application):
        os.umask(0o077)
        config = settings or Settings.from_env()
        # Keep the password provider injectable for isolated security tests.
        provider = passwords
        if provider is None:
            from passwords import Passwords
            provider = Passwords()
        application.state.settings = config
        application.state.store = store = Store(config.data)
        application.state.auth = auth = Auth(store, config, provider)
        auth.bootstrap(os.environ.pop("ADMIN_PASSWORD", None))
        store.recover()
        application.state.worker = worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="splice")
        application.state.whisper = whisper = WhisperWorker(config.data)

        async def sweep():
            while True:
                await asyncio.sleep(600)
                await run_in_threadpool(store.sweep)

        sweeper = asyncio.create_task(sweep())
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            worker.shutdown(wait=True, cancel_futures=True)
            whisper.close()

    application = FastAPI(title="Splice", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(SecurityMiddleware, state=application.state)
    # Prefix is known before startup; /tools is an actual route prefix, not merely
    # an OpenAPI root_path. The upstream proxy must preserve it.
    base = settings.base_path if settings else os.environ.get("SPLICE_BASE_PATH", "")

    def page(name):
        config = application.state.settings
        return HTMLResponse((ROOT / "static" / name).read_text().replace("__BASE__", config.base_path))

    @application.get(base + "/")
    def index():
        return page("index.html")

    @application.get(base + "/login")
    def login_page():
        return page("login.html")

    @application.get(base + "/signup")
    def signup_page():
        return page("signup.html")

    @application.get(base + "/signup/thanks")
    def signup_thanks():
        return page("signup-thanks.html")

    @application.get(base + "/admin")
    def admin_page():
        return page("admin.html")

    @application.get(base + "/static/{name}")
    def static(name: str):
        if name not in {"styles.css", "app.js", "login.js", "signup.js", "admin.js", "favicon.svg"}:
            raise HTTPException(404, "Not found.")
        return FileResponse(ROOT / "static" / name)

    @application.api_route(base + "/api/health", methods=["GET", "HEAD"])
    def health():
        return {"status": "ok"}

    @application.post(base + "/api/signup", status_code=202)
    async def signup(request: Request):
        body = await body_json(request)
        await run_in_threadpool(application.state.auth.signup, body.get("email"), body.get("password"), request.state.ip)
        return {"ok": True}

    @application.post(base + "/api/login")
    async def login(request: Request):
        body = await body_json(request)
        auth = application.state.auth
        user = await run_in_threadpool(auth.authenticate, normalise_email(body.get("email")),
                                      normalise_password(body.get("password")), request.state.ip)
        response = JSONResponse({"ok": True})
        auth.issue(user, request.cookies.get(application.state.settings.cookie_name), response)
        return response

    @application.get(base + "/api/session")
    def session(request: Request):
        user = request.state.user
        return {"email": user["email"], "admin": user["email"] == ADMIN_EMAIL}

    @application.post(base + "/api/logout")
    def logout(request: Request):
        response = JSONResponse({"ok": True})
        application.state.auth.logout(request.cookies.get(application.state.settings.cookie_name), response)
        return response

    @application.post(base + "/api/password")
    async def change_password(request: Request):
        body = await body_json(request)
        auth, store = application.state.auth, application.state.store
        user = request.state.user
        await run_in_threadpool(auth.reauthenticate, user, body.get("password"), request.state.ip)
        password = normalise_password(body.get("newPassword"))
        if not valid_password(password):
            raise HTTPException(400, "Use a password of 15–128 characters; avoid common passwords.")
        hashed = await run_in_threadpool(auth.passwords.hash, password)
        with store.db() as db:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            auth.audit(db, user["email"], "change-password", user["email"])
        response = JSONResponse({"ok": True})
        auth.issue(user, None, response)
        return response

    def present_job(payload):
        # URLs belong to this deployment, not the saved job. Also repair older
        # complete records that predate originalUrl without rewriting storage.
        if payload["status"] == "complete":
            config = application.state.settings
            payload["originalUrl"] = config.url(f"/media/{payload['jobId']}/original")
            for cut in payload["cuts"]:
                cut["videoUrl"] = config.url(f"/media/{payload['jobId']}/clip-{cut['index']:03d}.mp4")
        return payload

    @application.get(base + "/api/jobs")
    def recent(request: Request):
        return {"jobs": [present_job(job) for job in application.state.store.recent(request.state.user["id"])]}

    @application.get(base + "/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        return present_job(application.state.store.job(request.state.user["id"], job_id))

    @application.delete(base + "/api/jobs/{job_id}")
    def delete_job(job_id: str, request: Request):
        application.state.store.delete(request.state.user["id"], job_id)
        return {"ok": True}

    @application.delete(base + "/api/jobs")
    async def delete_all_jobs(request: Request):
        body = await body_json(request)
        user = request.state.user
        await run_in_threadpool(application.state.auth.reauthenticate, user, body.get("password"), request.state.ip)
        store = application.state.store
        with store.lock:
            jobs = store.recent(user["id"])
            if any(job["status"] in ("uploading", "queued", "processing") for job in jobs):
                raise HTTPException(409, "Wait for preparation to finish before deleting all videos.")
            for job in jobs:
                store.delete(user["id"], job["jobId"])
        return {"ok": True}

    @application.api_route(base + "/api/jobs/{job_id}/transcript/{cut_id}", methods=["GET", "PUT"])
    async def transcript(job_id: str, cut_id: str, request: Request):
        store = application.state.store
        owner = request.state.user["id"]
        # Authorise before parsing a write, including when the cut does not exist.
        payload = store.job(owner, job_id)
        cut = next((c for c in payload["cuts"] if c["id"] == cut_id), None)
        if cut is None:
            raise HTTPException(404, "Cut not found.")
        if request.method == "GET":
            return {"transcript": cut["transcript"]}
        body = await body_json(request)
        text = body.get("transcript")
        if not isinstance(text, str) or len(text) > 20000:
            raise HTTPException(400, "Use a transcript of at most 20,000 characters.")
        with store.lock:
            payload = store.job(owner, job_id)
            cut = next(c for c in payload["cuts"] if c["id"] == cut_id)
            cut["transcript"] = text
            store.update(owner, job_id, cuts=payload["cuts"])
        return {"transcript": text}

    @application.api_route(base + "/media/{job_id}/{filename}", methods=["GET", "HEAD"])
    def media(job_id: str, filename: str, request: Request):
        store = application.state.store
        owner = request.state.user["id"]
        job = store.job(owner, job_id)
        if job["status"] != "complete":
            raise HTTPException(404, "Media not found.")
        directory = store.directory(owner, job_id)
        if filename == "original":
            path = directory / "source.bin"
            suffix = job["sourceSuffix"]
            media_type = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}[suffix]
            download_name = "splice-original" + suffix
        elif filename in {f"clip-{cut['index']:03d}.mp4" for cut in job["cuts"]}:
            path, media_type, download_name = directory / filename, "video/mp4", filename
        else:
            raise HTTPException(404, "Media not found.")
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
            raise HTTPException(404, "Media not found.")
        # FileResponse serves ranges only after the same owner check as full reads.
        return FileResponse(path, media_type=media_type, filename=download_name, content_disposition_type="inline")

    @application.post(base + "/api/analyze-file", status_code=202)
    async def upload(request: Request):
        store, auth = application.state.store, application.state.auth
        owner = request.state.user["id"]
        auth.limit(("upload-user:" + owner, 5, 3600), ("upload-ip:" + request.state.ip, 10, 3600))
        job_id = store.new_job(owner)
        directory = store.directory(owner, job_id)
        try:
            async with asyncio.timeout(300):
                filename = await receive_upload(request, directory / "source.bin")
            suffix = Path(filename).suffix.lower()
            store.update(owner, job_id, status="queued", stage="Waiting for preparation…", originalFilename=filename, sourceSuffix=suffix)
            application.state.worker.submit(process_job, store, application.state.settings, application.state.whisper, owner, job_id, suffix)
        except BaseException as error:
            if directory.exists():
                shutil.rmtree(directory)
            store.update(owner, job_id, status="failed", stage="Upload failed", error="The upload was not accepted.")
            if isinstance(error, (HTTPException, asyncio.CancelledError)):
                raise
            raise HTTPException(400, "The upload was interrupted. Try again.") from None
        return present_job(store.job(owner, job_id))

    @application.get(base + "/api/admin/users")
    def users():
        with application.state.store.db() as db:
            rows = db.execute("SELECT email,enabled,status FROM users WHERE enabled=1 OR status='pending' ORDER BY email").fetchall()
        return {"users": [dict(row) for row in rows]}

    @application.post(base + "/api/admin/users")
    async def manage_access(request: Request):
        body = await body_json(request)
        store, auth = application.state.store, application.state.auth
        actor = request.state.user
        await run_in_threadpool(auth.reauthenticate, actor, body.get("password"), request.state.ip)
        email = normalise_email(body.get("email"))
        action = body.get("action")
        if not valid_email(email) or action not in ("accept", "decline", "remove"):
            raise HTTPException(400, "Choose an applicant to accept or decline, or an account to remove.")
        if email == ADMIN_EMAIL:
            raise HTTPException(400, "The administrator's access cannot be changed here.")
        with store.db() as db:
            row = db.execute("SELECT id,enabled,status FROM users WHERE email=?", (email,)).fetchone()
            if not row:
                raise HTTPException(404, "Account not found.")
            if (action in ("accept", "decline") and row["status"] != "pending") or (action == "remove" and not row["enabled"]):
                raise HTTPException(409, "This account has changed. Refresh the page.")
            status = {"accept": "approved", "decline": "declined", "remove": "removed"}[action]
            db.execute("UPDATE users SET enabled=?,status=? WHERE id=?", (int(action == "accept"), status, row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            auth.audit(db, actor["email"], action, email)
        return {"ok": True}

    return application


app = create_app()
