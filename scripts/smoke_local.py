"""Synthetic end-to-end smoke test; no network and no new dependencies."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SPLICE_OFFLINE"] = "1"

from fastapi.testclient import TestClient

import app


def wait_for_job(client, response):
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] == "failed":
            raise AssertionError(result)
        if result["status"] == "complete":
            return result
        time.sleep(0.1)
    raise AssertionError("The synthetic job did not finish within 90 seconds.")


def verify(client, result):
    cuts = result["cuts"]
    assert len(cuts) == 3, f"Expected 3 cuts, got {len(cuts)}: {result}"
    for index, cut in enumerate(cuts):
        assert cut["index"] == index + 1
        assert abs(cut["start"] - index * 1.25) < 0.05, cut
        assert abs(cut["duration"] - 1.25) < 0.05, cut
        assert isinstance(cut["transcript"], str)
        response = client.get(cut["videoUrl"])
        assert response.status_code == 200 and response.content[4:8] == b"ftyp"
        response = client.get(cut["videoUrl"], headers={"Range": "bytes=0-63"})
        assert response.status_code == 206 and len(response.content) == 64
        clip_path = app.MEDIA_DIR / result["jobId"] / f"clip-{index + 1:03d}.mp4"
        duration, _ = app.probe_video(clip_path)
        assert abs(duration - cut["duration"]) < 0.05, (duration, cut)
        subprocess.run([app.FFMPEG, "-v", "error", "-i", str(clip_path), "-f", "null", "-"], check=True, capture_output=True)
        pixel = subprocess.run([
            app.FFMPEG, "-v", "error", "-i", str(clip_path), "-frames:v", "1", "-vf", "scale=1:1",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
        ], check=True, capture_output=True).stdout
        red, green, blue = pixel
        if index == 0:
            assert red > 180 and green < 50 and blue < 50, pixel
        elif index == 1:
            assert min(pixel) > 220, pixel
        else:
            assert max(pixel) < 30, pixel
    return cuts


def main():
    sample_dir = ROOT / "data" / "sample"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample = sample_dir / "hard-cuts.mp4"
    command = [app.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for colour in ("red", "white", "black"):
        command.extend(["-f", "lavfi", "-i", f"color=c={colour}:s=360x640:r=24:d=1.25"])
    command.extend([
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]", "-map", "[v]",
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-movflags", "+faststart", str(sample),
    ])
    subprocess.run(command, check=True, capture_output=True)
    with TestClient(app.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/health").json()["sceneThreshold"] == 0.3
        local = wait_for_job(client, client.post("/api/analyze", json={"filePath": "data/sample/hard-cuts.mp4"}))
        verify(client, local)
        with sample.open("rb") as video:
            uploaded = wait_for_job(client, client.post("/api/analyze-file", files={"file": ("hard-cuts.mp4", video, "video/mp4")}))
        cuts = verify(client, uploaded)
        # Exercise the audio/Whisper branch with silent audio, still strictly offline.
        silent = sample_dir / "silent-audio.mp4"
        subprocess.run([
            app.FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(sample), "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(silent),
        ], check=True, capture_output=True)
        audio_result = wait_for_job(client, client.post("/api/analyze", json={"url": "data/sample/silent-audio.mp4"}))
        verify(client, audio_result)
        assert all(cut["transcript"] == "" for cut in local["cuts"])
        print("PASS: local path, multipart upload, queued jobs, 3 accurate scene clips, MP4 decoding, clip colours and HTTP byte ranges.")
        print("PASS: silent-audio job completed offline; transcript strings present on every cut.")
        if audio_result["warnings"]:
            print("Whisper: no cached model available; graceful empty-transcript fallback verified. No model download attempted.")
        else:
            print("Whisper: cached local model ran successfully on silent audio.")
        print(json.dumps({"jobId": uploaded["jobId"], "durations": [cut["duration"] for cut in cuts]}))


if __name__ == "__main__":
    main()
