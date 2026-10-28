"""Bounded local decoding, with no shell, network inputs or platform secrets."""
import json
import math
import os
import re
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

from config import MAX_BYTES, MAX_SECONDS, ROOT

# Similar screen shots can fall below 0.30. The sample's Claude
# jump cut scores 0.263 at 4.667s; 0.25 catches it while ignoring camera motion.
SCENE_THRESHOLD = 0.25
MIN_SCENE_SECONDS = 0.12
INPUT_OPTIONS = ["-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm", "-probesize", "10000000", "-analyzeduration", "5000000", "-threads", "2"]


class JobError(Exception):
    """Only fixed, safe messages may be returned to the browser."""


def tool_path(name):
    # Deployment binaries are fixed. Developer machines may use the package manager path.
    candidates = (f"/usr/bin/{name}", f"/opt/homebrew/bin/{name}")
    for path in candidates:
        if Path(path).is_file():
            return path
    raise RuntimeError(f"Required media tool missing: {name}")


FFMPEG = tool_path("ffmpeg")
FFPROBE = tool_path("ffprobe")


def worker_env(directory):
    # Never inherit SESSION_SECRET, bootstrap passwords or hosting credentials.
    return {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(directory), "TMPDIR": str(directory),
            "LANG": "C.UTF-8", "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}


def safe_path(path, directory):
    path = Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
        raise JobError("Invalid media path.")
    return str(path.resolve())


def run(command, directory, deadline, error, timeout=120):
    remaining = min(timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise JobError("Preparation timed out. Try a shorter video.")
    try:
        return subprocess.run(command, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, check=True, timeout=remaining, cwd=directory, env=worker_env(directory))
    except (subprocess.SubprocessError, OSError):
        raise JobError(error) from None


def probe_video(source, directory, deadline, suffix):
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise JobError("Choose a non-empty video up to 200 MiB.")
    with source.open("rb") as stream:
        beginning = stream.read(4096)
        stream.seek(max(0, source.stat().st_size - 4096))
        ending = stream.read(4096)
        magic = beginning[:16]
    if any(marker in (beginning + ending).lower() for marker in (b"<!doctype html", b"<script", b"<?php", b"#extm3u")):
        raise JobError("This file contains non-video content.")
    if suffix in (".mp4", ".mov") and magic[4:8] not in (b"ftyp", b"moov", b"mdat", b"wide"):
        raise JobError("The file does not match its video extension.")
    if suffix == ".webm" and magic[:4] != b"\x1aE\xdf\xa3":
        raise JobError("The file does not match its video extension.")
    result = run([FFPROBE, "-hide_banner", "-loglevel", "error", "-max_alloc", "67108864", *INPUT_OPTIONS,
                  "-show_format", "-show_streams", "-of", "json", safe_path(source, directory)],
                 directory, deadline, "This file could not be read as a video.", timeout=20)
    try:
        metadata = json.loads(result.stdout)
        streams = metadata["streams"]
        formats = set(metadata["format"]["format_name"].split(","))
        expected = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"} if suffix in (".mp4", ".mov") else {"matroska", "webm"}
        if not formats or not formats <= expected or len(streams) > 8:
            raise ValueError
        videos = [s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
        if not videos:
            raise ValueError
        durations = [float(metadata["format"]["duration"])]
        for stream in streams:
            if "duration" in stream:
                durations.append(float(stream["duration"]))
            if stream["codec_type"] == "video":
                if suffix == ".webm" and stream["codec_name"] not in {"vp8", "vp9", "av1"}:
                    raise ValueError
                if stream["codec_name"] not in {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "prores"}:
                    raise ValueError
                if not (2 <= int(stream["width"]) <= 1920 and 2 <= int(stream["height"]) <= 1920):
                    raise ValueError
                numerator, denominator = map(float, stream.get("avg_frame_rate", "0/1").split("/"))
                rate = numerator / denominator
                if not math.isfinite(rate) or not 0 < rate <= 60:
                    raise ValueError
            elif stream["codec_type"] == "audio":
                if suffix == ".webm" and stream["codec_name"] not in {"vp8", "vp9", "av1"}:
                    raise ValueError
                if stream["codec_name"] not in {"aac", "opus", "vorbis", "mp3", "pcm_s16le", "pcm_s24le"} or int(stream.get("channels", 0)) > 8:
                    raise ValueError
        if not all(math.isfinite(v) and MIN_SCENE_SECONDS <= v <= MAX_SECONDS for v in durations):
            raise ValueError
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        raise JobError("Use an MP4, MOV or WebM up to 5 minutes, 1920 × 1920 and 60 fps, with a supported video codec.") from None
    return max(durations), any(s["codec_type"] == "audio" for s in streams)


def cut_boundaries(source, duration, directory, deadline):
    # showinfo needs info logging; all other decoder calls use error-only logging.
    result = run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-y", "-max_alloc", "67108864", *INPUT_OPTIONS,
                  "-i", safe_path(source, directory), "-map", "0:v:0", "-t", str(MAX_SECONDS),
                  "-vf", f"setpts=PTS-STARTPTS,select='gt(scene,{SCENE_THRESHOLD:.2f})',showinfo",
                  "-an", "-fps_mode", "vfr", "-f", "null", "-fs", str(MAX_BYTES), "/dev/null"],
                 directory, deadline, "The cuts could not be detected.")
    candidates = sorted({float(v) for v in re.findall(r"\bpts_time:([0-9.eE+-]+)", result.stderr)})
    boundaries = [0.0]
    for timestamp in candidates:
        if timestamp - boundaries[-1] >= MIN_SCENE_SECONDS and duration - timestamp >= MIN_SCENE_SECONDS:
            boundaries.append(timestamp)
    boundaries.append(duration)
    if len(boundaries) > 201:
        raise JobError("This video has more than 200 cuts. Try a shorter video.")
    return boundaries


class WhisperWorker:
    """One reusable model in a killable child; JSON over pipes, never shell text."""
    def __init__(self, data):
        self.data = data
        self.process = None

    def close(self):
        if self.process is not None:
            self.process.kill()
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None

    def transcribe(self, audio, cuts, directory, deadline):
        if self.process is None:
            self.process = subprocess.Popen([sys.executable, str(ROOT / "whisper_worker.py"), str(self.data / "models"), str(self.data / "uploads")],
                                            shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            cwd=self.data, env=worker_env(self.data))
        try:
            request = {"audio": safe_path(audio, directory), "cuts": [{"start": c["start"], "end": c["end"]} for c in cuts]}
            self.process.stdin.write(json.dumps(request).encode() + b"\n")
            self.process.stdin.flush()
            end = min(deadline, time.monotonic() + 180)
            output = b""
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while b"\n" not in output and len(output) <= 262144:
                    if not selector.select(max(0, end - time.monotonic())):
                        raise TimeoutError
                    chunk = os.read(self.process.stdout.fileno(), 8192)
                    if not chunk:
                        raise EOFError
                    output += chunk
            response = json.loads(output)
            if not response.get("ok") or len(response["transcripts"]) != len(cuts):
                raise ValueError
            return response["transcripts"]
        except Exception:
            self.close()
            raise JobError("Transcription is unavailable. You can still type your own words.") from None


def process_job(store, settings, whisper, owner, job_id, suffix):
    directory = store.directory(owner, job_id)
    source = directory / "source.bin"
    deadline = time.monotonic() + 300
    try:
        store.update(owner, job_id, status="processing", stage="Reading video…", progress=5)
        duration, has_audio = probe_video(source, directory, deadline, suffix)
        store.update(owner, job_id, stage="Finding cuts…", progress=15)
        boundaries = cut_boundaries(source, duration, directory, deadline)
        cuts = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
            filename = f"clip-{index:03d}.mp4"
            store.update(owner, job_id, stage=f"Creating cut {index} of {len(boundaries)-1}…", progress=20 + round(55 * index / len(boundaries)))
            run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-max_alloc", "67108864", "-y", "-ss", f"{start:.6f}",
                 *INPUT_OPTIONS, "-i", safe_path(source, directory), "-t", f"{end-start:.6f}", "-fs", str(MAX_BYTES),
                 "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                 safe_path(directory / filename, directory)], directory, deadline, "A cut could not be prepared.")
            if sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) > 2 * MAX_BYTES:
                raise JobError("The prepared video exceeds the storage limit. Try a smaller video.")
            cuts.append({"id": f"{job_id}-{index}", "index": index, "start": start, "end": end, "duration": end-start,
                         "videoUrl": settings.url(f"/media/{job_id}/{filename}"), "transcript": "", "originalTranscript": ""})
        warnings = []
        store.update(owner, job_id, stage="Transcribing…", progress=80)
        if has_audio:
            audio = directory / "speech.wav"
            try:
                run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *INPUT_OPTIONS,
                     "-i", safe_path(source, directory), "-t", str(MAX_SECONDS), "-fs", "10000000", "-map", "0:a:0",
                     "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", safe_path(audio, directory)],
                    directory, deadline, "Audio could not be prepared.")
                for cut, transcript in zip(cuts, whisper.transcribe(audio, cuts, directory, deadline)):
                    cut.update(transcript=transcript, originalTranscript=transcript)
            except JobError as error:
                warnings.append(str(error))
            finally:
                audio.unlink(missing_ok=True)
        else:
            warnings.append("This video has no audio track. Type the words you'll record.")
        store.update(owner, job_id, status="complete", stage="Your cut sheet is ready", progress=100, cuts=cuts,
                     warnings=warnings, sourceDuration=duration, originalUrl=settings.url(f"/media/{job_id}/original"))
    except Exception as error:
        if directory.exists():
            shutil.rmtree(directory)
        store.update(owner, job_id, status="failed", stage="Preparation stopped", cuts=[],
                     error=str(error) if isinstance(error, JobError) else "Preparation failed. Try another video.")
