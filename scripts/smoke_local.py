"""Offline in-process smoke: synthetic media and synthetic test identities only."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient
from app import create_app
from config import Settings
from pipeline import FFMPEG, FFPROBE
from test_app import CSRF


def command(argv):
    return subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60)


def wait_for_job(client, response):
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["status"] == "failed":
            raise AssertionError(result["error"])
        if result["status"] == "complete":
            return result
        time.sleep(.05)
    raise AssertionError("Synthetic video did not finish within 90 seconds.")


def verify(client, store, owner, result):
    cuts = result["cuts"]
    assert len(cuts) == 3, f"Expected three cuts, got {len(cuts)}"
    for index, cut in enumerate(cuts):
        assert abs(cut["start"] - index * 1.25) < .05
        assert abs(cut["duration"] - 1.25) < .05
        assert isinstance(cut["transcript"], str)
        response = client.get(cut["videoUrl"])
        assert response.status_code == 200 and response.content[4:8] == b"ftyp"
        response = client.get(cut["videoUrl"], headers={"Range": "bytes=0-63"})
        assert response.status_code == 206 and len(response.content) == 64
        path = store.directory(owner, result["jobId"]) / f"clip-{index+1:03d}.mp4"
        metadata = json.loads(command([FFPROBE, "-v", "error", "-protocol_whitelist", "file", "-show_format", "-of", "json", str(path)]).stdout)
        assert abs(float(metadata["format"]["duration"]) - cut["duration"]) < .1
        pixel = command([FFMPEG, "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]).stdout
        if index == 0:
            assert pixel[0] > 180 and pixel[1] < 50 and pixel[2] < 50
        elif index == 1:
            assert min(pixel) > 220
        else:
            assert max(pixel) < 30
    source = store.directory(owner, result["jobId"]) / "source.bin"
    original = client.get(result["originalUrl"])
    assert original.status_code == 200 and original.content == source.read_bytes()
    head = client.head(result["originalUrl"])
    assert head.status_code == 200 and int(head.headers["content-length"]) == source.stat().st_size
    assert client.get(result["originalUrl"], headers={"Range": "bytes=0-31"}).status_code == 206
    metadata = json.loads(command([FFPROBE, "-v", "error", "-show_format", "-of", "json", str(source)]).stdout)
    assert abs(float(metadata["format"]["duration"]) - result["sourceDuration"]) < .1
    assert result["sourceDuration"] > max(cut["duration"] for cut in cuts)


def main():
    (ROOT / "data").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temporary:
        directory = Path(temporary)
        sample = directory / "hard-cuts.mp4"
        argv = [FFMPEG, "-nostdin", "-v", "error", "-y"]
        for colour in ("red", "white", "black"):
            argv.extend(["-f", "lavfi", "-i", f"color=c={colour}:s=360x640:r=24:d=1.25"])
        argv.extend(["-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", str(sample)])
        command(argv)
        config = Settings(directory / "private", "https://testserver", "", bytes(32), False)
        application = create_app(config)
        with TestClient(application, base_url=config.origin) as client:
            assert client.get("/api/jobs").status_code == 401
            assert client.post("/api/login", headers=CSRF, json={"email": "lukesa100@gmail.com"}).status_code == 200
            store = application.state.store
            with store.db() as db:
                owner = db.execute("SELECT id FROM users").fetchone()[0]
            with sample.open("rb") as stream:
                result = wait_for_job(client, client.post("/api/analyze-file", files={"file": ("$(id).mp4", stream, "video/mp4")}, headers=CSRF))
            verify(client, store, owner, result)
            assert result["originalFilename"] == "$(id).mp4"
            cut = result["cuts"][0]
            path = f"/api/jobs/{result['jobId']}/transcript/{cut['id']}"
            assert client.put(path, headers=CSRF, json={"transcript": "Synthetic edited words"}).status_code == 200
            assert client.get(path).json()["transcript"] == "Synthetic edited words"
            silent = directory / "silent.mp4"
            command([FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(sample), "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(silent)])
            with silent.open("rb") as stream:
                audio = wait_for_job(client, client.post("/api/analyze-file", files={"file": ("silent.mp4", stream, "video/mp4")}, headers=CSRF))
            verify(client, store, owner, audio)
            assert not list(store.uploads.glob("*/*/speech.wav"))
            assert audio["warnings"], "Missing offline model should give an editable fallback"
            # Real playlist input is rejected before expensive decoding.
            malicious = client.post("/api/analyze-file", files={"file": ("playlist.mp4", b"#EXTM3U\nhttp://127.0.0.1/private", "video/mp4")}, headers=CSRF)
            assert malicious.status_code == 202
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                rejected = client.get("/api/jobs/" + malicious.json()["jobId"]).json()
                if rejected["status"] == "failed":
                    break
                time.sleep(.05)
            assert rejected["status"] == "failed"
            assert not store.directory(owner, rejected["jobId"]).exists()
            assert client.delete("/api/jobs/" + result["jobId"], headers=CSRF).status_code == 200
            assert client.get(result["originalUrl"]).status_code == 404
        print("PASS: authenticated upload, three scene cuts, timings/colours, original and cut ranges, persisted transcript, hard deletion, playlist rejection and offline Whisper fallback.")


if __name__ == "__main__":
    main()
