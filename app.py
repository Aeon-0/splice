"""Local, ephemeral Instagram cut sheets. No model is used to find cuts."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
JOBS_DIR = DATA / "jobs"
MEDIA_DIR = DATA / "media"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
YT_DLP = "/Users/aeon/aeon-env/bin/yt-dlp"
SCENE_THRESHOLD = 0.30
MIN_SCENE_SECONDS = 0.12
MAX_BYTES = 500 * 1024 * 1024
MAX_SECONDS = 600
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
INPUT_OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,avi"]

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
whisper_model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app.state.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="splice")
    yield
    app.state.worker.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Splice", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
# Only finished clips are public: sources, audio, model files and cookies are not here.
app.mount("/media", StaticFiles(directory=MEDIA_DIR, check_dir=False), name="media")


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin:
        try:
            allowed = urlsplit(origin).netloc == request.headers.get("host")
        except ValueError:
            allowed = False
        if not allowed:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Open Splice directly to submit a video."}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "sceneThreshold": SCENE_THRESHOLD}


class AnalyseInput(BaseModel):
    url: str | None = None
    filePath: str | None = None
    cookiesPath: str | None = None


class JobError(Exception):
    """An error whose message can safely be shown to a user."""


def instagram_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid_host = parsed.hostname in {"instagram.com", "www.instagram.com", "m.instagram.com"}
        match = re.fullmatch(r"/reels?/([A-Za-z0-9_-]{1,128})/?", parsed.path)
        valid = parsed.scheme == "https" and valid_host and match and not parsed.username and parsed.port is None
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, "Paste a public Instagram reel URL, starting with https://www.instagram.com/reel/.")
    return f"https://www.instagram.com/reel/{match.group(1)}/"


def local_video(value: str) -> Path:
    path = Path(value).expanduser()
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        raise HTTPException(400, "Local videos must be inside this project. Otherwise use Upload.") from None
    if any(part.startswith(".") for part in relative.parts) or path.suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(400, "Choose an MP4, MOV, M4V, WebM, MKV or AVI inside this project.")
    if not path.is_file():
        raise HTTPException(400, "That local video does not exist.")
    if path.stat().st_size > MAX_BYTES:
        raise HTTPException(413, "Videos must be smaller than 500 MiB.")
    return path


def cookies_file(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "The cookies path must be absolute and outside the project.")
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise HTTPException(400, "Keep cookies outside the project.")
    if not path.is_file():
        raise HTTPException(400, "The cookies file was not found on the server.")
    return path


def new_job() -> str:
    with jobs_lock:
        if len(jobs) >= 100:
            raise HTTPException(429, "This session has reached 100 jobs. Restart the server to begin a new session.")
        job_id = uuid.uuid4().hex
        (JOBS_DIR / job_id).mkdir(parents=True)
        (MEDIA_DIR / job_id).mkdir(parents=True)
        jobs[job_id] = {
            "jobId": job_id, "status": "queued", "stage": "Waiting for the local worker…",
            "progress": 0, "cuts": [], "warnings": [], "error": None,
        }
    return job_id


def update_job(job_id: str, **changes):
    with jobs_lock:
        jobs[job_id].update(changes)


def snapshot(job_id: str) -> dict:
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found. Jobs expire when the server restarts.")
        return copy.deepcopy(jobs[job_id])


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return snapshot(job_id)


@app.post("/api/analyze", status_code=202)
def analyse(body: AnalyseInput, request: Request, cookiesPath: str | None = None):
    if bool(body.url) == bool(body.filePath):
        raise HTTPException(400, "Provide one reel URL or one local filePath.")
    value = (body.filePath or body.url or "").strip()
    if not value:
        raise HTTPException(400, "Choose a video first.")
    is_url = not body.filePath and value.lower().startswith(("http://", "https://"))
    source = instagram_url(value) if is_url else local_video(value)
    cookies = cookies_file(body.cookiesPath or cookiesPath or os.environ.get("SPLICE_COOKIES") or os.environ.get("UGC_CUTS_COOKIES") or os.environ.get("RESPLICE_COOKIES") or os.environ.get("PACEAI_COOKIES")) if is_url else None
    job_id = new_job()
    request.app.state.worker.submit(process_job, job_id, source, is_url, cookies)
    return snapshot(job_id)


async def receive_upload(request: Request, destination: Path):
    """Stream one multipart `file` part, with bounded header/tail buffers.

    Deliberately accepts only the single-file form used by this UI and curl -F.
    This avoids adding python-multipart to the existing environment.
    """
    content_type = Message()
    content_type["content-type"] = request.headers.get("content-type", "")
    boundary = content_type.get_param("boundary")
    if content_type.get_content_type() != "multipart/form-data" or not boundary:
        raise HTTPException(400, "Upload a video as multipart/form-data with one field named file.")
    if not re.fullmatch(r"[\x21-\x7e]{1,70}", boundary):
        raise HTTPException(400, "Invalid upload boundary.")
    opening = b"--" + boundary.encode("ascii") + b"\r\n"
    marker = b"\r\n--" + boundary.encode("ascii")
    pending = b""
    state = "headers"
    size = 0
    with destination.open("xb") as output:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BYTES + 16384:
                raise HTTPException(413, "Videos must be smaller than 500 MiB.")
            pending += chunk
            if state == "headers":
                end = pending.find(b"\r\n\r\n")
                if end < 0:
                    if len(pending) > 16384:
                        raise HTTPException(400, "Upload headers are too large.")
                    continue
                if end > 16384 or not pending.startswith(opening):
                    raise HTTPException(400, "Invalid upload headers.")
                headers = Message()
                for line in pending[len(opening):end].decode("latin-1").split("\r\n"):
                    name, separator, value = line.partition(":")
                    if not separator:
                        raise HTTPException(400, "Invalid upload headers.")
                    headers[name] = value.strip()
                name = headers.get_param("name", header="content-disposition")
                filename = headers.get_filename()
                if headers.get_content_disposition() != "form-data" or name != "file" or not filename:
                    raise HTTPException(400, "Use a single video field named file.")
                if Path(filename).suffix.lower() not in VIDEO_SUFFIXES:
                    raise HTTPException(400, "Upload an MP4, MOV, M4V, WebM, MKV or AVI.")
                pending = pending[end + 4:]
                state = "file"
            if state == "file":
                end = pending.find(marker)
                if end >= 0:
                    output.write(pending[:end])
                    pending = pending[end + len(marker):]
                    state = "end"
                elif len(pending) > len(marker):
                    output.write(pending[:-len(marker)])
                    pending = pending[-len(marker):]
            if state == "end" and len(pending) > 4:
                raise HTTPException(400, "Upload one file at a time, with no additional form fields.")
        if state != "end" or pending not in (b"--", b"--\r\n"):
            raise HTTPException(400, "The upload was incomplete. Try again.")
        if not 0 < output.tell() <= MAX_BYTES:
            raise HTTPException(413, "Choose a non-empty video smaller than 500 MiB.")


@app.post("/api/analyze-file", status_code=202)
async def analyse_file(request: Request):
    job_id = new_job()
    source = JOBS_DIR / job_id / "source.upload"
    update_job(job_id, stage="Receiving your video…")
    try:
        await receive_upload(request, source)
    except HTTPException:
        update_job(job_id, status="failed", stage="Upload failed", error="The upload was not accepted.")
        raise
    except Exception:
        update_job(job_id, status="failed", stage="Upload failed", error="The upload was interrupted.")
        raise HTTPException(400, "The upload was interrupted. Try again.") from None
    request.app.state.worker.submit(process_job, job_id, source, False, None)
    return snapshot(job_id)


def run(command: list[str], error: str, timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        # Downloader output can contain private account details; never return or log it.
        raise JobError(error) from None


def download_reel(url: str, directory: Path, cookies: Path | None) -> Path:
    command = [
        YT_DLP, "--ignore-config", "--no-playlist", "--no-progress", "--restrict-filenames",
        "--socket-timeout", "20", "--retries", "1", "--fragment-retries", "1",
        "--max-filesize", "500M", "--match-filter", "duration <= 600",
        "--format", "bv*+ba/b", "--merge-output-format", "mp4", "--remux-video", "mp4",
        "--ffmpeg-location", str(Path(FFMPEG).parent), "--output", str(directory / "source.%(ext)s"),
    ]
    if cookies:
        command.extend(["--cookies", str(cookies)])
    command.append(url)
    run(command, "Instagram could not download this reel. It may require fresh browser cookies, be restricted, or be rate-limited. Try Upload or a local file.", timeout=300)
    source = directory / "source.mp4"
    if not source.is_file() or source.stat().st_size == 0:
        raise JobError("Instagram returned no video. Try fresh cookies or upload a local video.")
    return source


def probe_video(source: Path) -> tuple[float, bool]:
    result = run([
        FFPROBE, "-v", "error", *INPUT_OPTIONS, "-show_format", "-show_streams", "-of", "json", str(source),
    ], "This file could not be read as a video.", timeout=30)
    try:
        metadata = json.loads(result.stdout)
        video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic"))
        duration = float(video.get("duration") or metadata["format"]["duration"])
        if not MIN_SCENE_SECONDS <= duration <= MAX_SECONDS:
            raise ValueError
    except (ValueError, KeyError, StopIteration, TypeError):
        raise JobError("Choose a video with a readable duration of up to 10 minutes.") from None
    return duration, any(stream["codec_type"] == "audio" for stream in metadata["streams"])


def cut_boundaries(source: Path, duration: float) -> list[float]:
    result = run([
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", *INPUT_OPTIONS, "-i", str(source),
        "-map", "0:v:0", "-vf", f"setpts=PTS-STARTPTS,select='gt(scene,{SCENE_THRESHOLD:.2f})',showinfo",
        "-an", "-fps_mode", "vfr", "-f", "null", "-",
    ], "ffmpeg could not detect cuts in this video.")
    candidates = sorted({float(value) for value in re.findall(r"\bpts_time:([0-9.eE+-]+)", result.stderr)})
    boundaries = [0.0]
    for timestamp in candidates:
        if timestamp - boundaries[-1] >= MIN_SCENE_SECONDS and duration - timestamp >= MIN_SCENE_SECONDS:
            boundaries.append(timestamp)
    boundaries.append(duration)
    if len(boundaries) > 201:
        raise JobError("This video has more than 200 cuts. Try a shorter reel.")
    return boundaries


def split_clip(source: Path, destination: Path, start: float, duration: float):
    run([
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.6f}", *INPUT_OPTIONS, "-i", str(source), "-t", f"{duration:.6f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(destination),
    ], "ffmpeg could not create a clip.")


def slice_transcript(segments, cuts: list[dict]) -> list[str]:
    """Assign each word once, to the cut containing its temporal midpoint."""
    buckets: list[list[str]] = [[] for _ in cuts]
    for segment in segments:
        words = getattr(segment, "words", None)
        tokens = [(word.start, word.end, word.word) for word in words] if words else [(segment.start, segment.end, segment.text)]
        for start, end, text in tokens:
            midpoint = (start + end) / 2
            for index, cut in enumerate(cuts):
                if cut["start"] <= midpoint < cut["end"]:
                    buckets[index].append(text)
                    break
    return ["".join(words).strip() for words in buckets]


def transcribe(source: Path, cuts: list[dict], directory: Path) -> list[str]:
    global whisper_model
    audio = directory / "speech.wav"
    run([
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *INPUT_OPTIONS,
        "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
    ], "The audio could not be prepared for local transcription.")
    # All model caches stay under data/. Do not use saved Hugging Face credentials.
    os.environ.setdefault("HF_HOME", str(DATA / "huggingface"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    from faster_whisper import WhisperModel

    if whisper_model is None:
        whisper_model = WhisperModel(
            os.environ.get("SPLICE_WHISPER_MODEL") or os.environ.get("UGC_CUTS_WHISPER_MODEL") or "base", device="cpu", compute_type="int8",
            cpu_threads=min(os.cpu_count() or 2, 4), num_workers=1,
            download_root=str(DATA / "models"), local_files_only=(os.environ.get("SPLICE_OFFLINE") or os.environ.get("UGC_CUTS_OFFLINE")) == "1",
        )
    segments, _ = whisper_model.transcribe(str(audio), beam_size=5, word_timestamps=True, vad_filter=True)
    return slice_transcript(segments, cuts)


def process_job(job_id: str, source: str | Path, remote: bool, cookies: Path | None):
    directory = JOBS_DIR / job_id
    try:
        update_job(job_id, status="processing", stage="Downloading reel…" if remote else "Reading video…", progress=5)
        if remote:
            source = download_reel(str(source), directory, cookies)
        else:
            source = Path(source)
            # Snapshot local inputs so later edits to the original cannot change a running job.
            if source.parent != directory:
                copied = directory / "source.upload"
                shutil.copyfile(source, copied)
                source = copied
        if source.stat().st_size > MAX_BYTES:
            raise JobError("Videos must be smaller than 500 MiB.")
        duration, has_audio = probe_video(source)
        update_job(job_id, stage="Finding hard cuts with ffmpeg…", progress=15)
        boundaries = cut_boundaries(source, duration)
        cuts = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
            update_job(job_id, stage=f"Creating clip {index} of {len(boundaries) - 1}…", progress=20 + round(55 * (index - 1) / (len(boundaries) - 1)))
            filename = f"clip-{index:03d}.mp4"
            split_clip(source, MEDIA_DIR / job_id / filename, start, end - start)
            cuts.append({
                "id": f"{job_id}-{index}", "index": index, "start": round(start, 6), "end": round(end, 6),
                "duration": round(end - start, 6), "videoUrl": f"/media/{job_id}/{filename}", "transcript": "",
            })
        warnings = []
        update_job(job_id, cuts=copy.deepcopy(cuts), sourceDuration=round(duration, 6), progress=80,
                   stage="Transcribing locally with Whisper (first use may download model weights)…" if has_audio else "Finishing cut sheet…")
        if has_audio:
            try:
                transcripts = transcribe(source, cuts, directory)
                for cut, transcript in zip(cuts, transcripts):
                    cut["transcript"] = transcript
            except Exception:
                # Cuts are still useful if model weights are unavailable, especially offline.
                warnings.append("Clips are ready, but local Whisper could not transcribe the audio. Check the model cache and SPLICE_OFFLINE setting; allow the free model download on first use. You can still type your own words.")
        else:
            warnings.append("This video has no audio track. Add your own words beside each clip.")
        update_job(job_id, status="complete", stage="Your cut sheet is ready", progress=100, cuts=cuts, warnings=warnings)
    except JobError as error:
        update_job(job_id, status="failed", stage="Analysis stopped", error=str(error))
    except Exception:
        update_job(job_id, status="failed", stage="Analysis stopped", error="The video could not be processed. Check the local tools and try another video.")
