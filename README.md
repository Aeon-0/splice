# Splice

Upload a reel or video from your phone or computer, get separate MP4s for its hard cuts, and write your own words beside each beat. **Upload is the only input in the MVP UI.** A mobile-friendly single page plays each selected clip on its own and shows its duration to two decimal places. Cut detection uses **ffmpeg only**. Local Whisper supplies an editable starting transcript; there is no script rewriting, LLM, paid API or vision model.

## Start and stop

From this project:

```sh
./start.sh
./stop.sh
```

- Local: **http://127.0.0.1:8787**
- LAN: **http://192.168.1.98:8787** (same network; the Mac must still have that address and permit incoming connections).
- Binds **0.0.0.0:8787**. Port **3100** is untouched. This is an unauthenticated local/LAN tool; use it on a trusted network.
- `start.sh` checks the port, launches a background process and waits for health. Repeat starts are safe. `stop.sh` checks the PID belongs to this project before sending SIGTERM. An active job may need time to finish.
- PID and logs: `data/server.pid`, `data/server.log`. Job status is held in memory; restarting clears the job list.

The existing environment is used directly; activation and dependency installation are unnecessary:

| Tool | Path / version used |
| --- | --- |
| Python | `/Users/aeon/aeon-env/bin/python` |
| Uvicorn | `/Users/aeon/aeon-env/bin/uvicorn` |
| FastAPI | 0.135.1 |
| faster-whisper | 1.2.1 |
| ffmpeg / ffprobe | `/opt/homebrew/bin/ffmpeg`, `/opt/homebrew/bin/ffprobe` |

There are no frontend package dependencies. The UI loads IBM Plex Sans and Mono from Google Fonts, with system font fallbacks. A bounded streaming reader handles the single-file multipart upload; `python-multipart` is not needed. To run in the foreground instead:

```sh
/Users/aeon/aeon-env/bin/uvicorn app:app --host 0.0.0.0 --port 8787
```

## Use it

1. Drop a saved video into the upload box or select **Choose video**. Preparation starts immediately, with **Uploading → Finding cuts → Transcribing → Ready** stages.
2. Select a thumbnail above the player to play that cut alone. Use the left/right arrow keys to select another cut and Enter to play it. Its duration and source time range appear beside the player, or below it on mobile. **Download cut** saves the selected MP4.
3. Edit **Edit what you'll say**. Drafts and the selected cut survive refreshes in the same browser tab using session storage. **Reset transcript** restores Whisper's text. Copy your words before closing the tab; there is no long-term save or transcript-edit API. **New upload** returns to the upload box.

Local paths remain available through `/api/analyze` for server-side smoke testing, outside the UI. Copy a video into `data/sample/` and submit its `filePath` as shown below. Server-side paths must resolve inside the project; uploads can come from anywhere on your device.

Inputs: MP4, MOV/M4V, WebM/MKV and AVI; maximum 500 MiB and 10 minutes. One worker processes jobs in order, up to 100 jobs per server session and 200 cuts per video. The UI opens the cut sheet once preparation finishes, including when transcription falls back to empty text. Processing limits and timeouts favour short reels.

## Exact hard-cut method

ffmpeg decodes the first video stream and runs:

```text
setpts=PTS-STARTPTS,select='gt(scene,0.30)',showinfo
```

**Threshold: scene score strictly greater than 0.30.** This is ffmpeg's frame-change metric, not an AI model. `showinfo` reports each selected frame's `pts_time`; resetting timestamps makes those times relative to the source start. Keep time 0, accepted cut times, then the video-stream duration. Ignore candidates within **0.12 seconds** of the previous accepted boundary or the video end to suppress extremely short fragments. If there are no detected cuts, return the whole video as one clip.

Each interval is re-encoded with accurate input seeking and an explicit duration: H.264 (`libx264`, `veryfast`, CRF 23, YUV420p) and AAC at 128 kbit/s, with `+faststart`. Stream copy is deliberately avoided so cuts do not depend on source keyframes. Dimensions are rounded down to even values for H.264 compatibility. Timing is accurate to the source frame grid; audio packet/container duration may differ slightly. The UI reports the source interval length, e.g. **4.53s**.

This detects hard changes, including flashes and large camera movements. Similar-looking jump cuts may be missed and fades are not reliably detected. The fixed threshold is declared as `SCENE_THRESHOLD` in `app.py`; changing it changes both detection and the health endpoint.

## Local speech recognition

The whole video's audio is extracted to 16 kHz mono PCM. `faster-whisper` runs **base**, **CPU**, **int8**, beam size 5, voice activity detection and word timestamps. Each recognised word goes to the cut containing its timestamp midpoint, so words straddling a cut appear once. If word timestamps are unavailable, the segment midpoint is used. Language is detected automatically. Whisper can mishear speech, music and silence; edit its result.

On the first audio-bearing video, the free open-source model weights download to `data/models/`; inference then runs locally. No video or audio is sent to a transcription service. Missing weights, offline mode or a transcription error produce a warning and empty editable transcripts while retaining all clips. Videos without an audio track skip Whisper. A single worker reuses the loaded model.

Optional environment settings, set **before** starting:

```sh
export SPLICE_WHISPER_MODEL=base  # tiny / small also supported; restart to change
export SPLICE_OFFLINE=1           # use cached weights only; never download
./start.sh
```

Omit `SPLICE_OFFLINE` (or set it to `0`) to allow the first model download. Model/cache files created by the app live under gitignored `data/`. The smoke script always forces offline mode.

## Instagram links are outside the public MVP

Reel-link paste and cookie controls have been removed from the UI. `/api/analyze` rejects HTTP(S) URLs with HTTP 400 and directs users to Upload; it neither reads cookies nor starts a download. Public Instagram downloads must not route through this Mac. Mac browser cookies, cookie exports and browser-cookie routing are intentionally not used. Legacy downloader helpers remain unused. Upload a video you already have saved instead.

## API

```sh
# Primary input: multipart upload, exactly one field named file, no extra form fields
curl -sS http://127.0.0.1:8787/api/analyze-file \
  -F 'file=@data/sample/hard-cuts.mp4'

# Secondary input for server-side testing: local path relative to the project
curl -sS http://127.0.0.1:8787/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"filePath":"data/sample/hard-cuts.mp4"}'

curl -sS http://127.0.0.1:8787/api/jobs/JOB_ID
```

POST returns HTTP 202 after the upload is received and processing is queued. Poll GET `/api/jobs/{id}` until `complete` or `failed`. Statuses: `queued`, `processing`, `complete`, `failed`. All job responses include this shape, with additional progress, stage, warnings and error fields:

```json
{
  "jobId": "example",
  "status": "complete",
  "cuts": [{
    "id": "example-1",
    "index": 1,
    "start": 0.0,
    "end": 4.53,
    "duration": 4.53,
    "videoUrl": "/media/example/clip-001.mp4",
    "transcript": "The words in this beat."
  }]
}
```

GET `/` serves the UI, `/api/health` checks readiness, `/docs` provides FastAPI's API documentation (its interactive assets need internet), and `/media/...` serves only generated clips, including byte-range seeking. Original uploads, extracted speech and model files are not exposed. Local filenames and raw tool errors are not returned. Cross-origin browser submissions are rejected; the API is intended to be used from this UI or local command-line tools.

## Checks and synthetic smoke

```sh
/Users/aeon/aeon-env/bin/python -m unittest discover -s tests -v
./scripts/smoke_local.sh
node --check static/app.js
node --test tests/test_ui.js
sh -n start.sh stop.sh scripts/smoke_local.sh
```

The smoke creates `data/sample/hard-cuts.mp4`: red → white → black, three 1.25-second vertical scenes. It exercises multipart upload first, then the local-path API using in-process HTTP (no listening socket), waits for completed jobs, verifies exactly three cuts, decodes every MP4, checks durations and the actual scene colours, and checks HTTP byte ranges. A second upload adds silent AAC audio to exercise the Whisper branch offline. Transcript strings must be present; empty synthetic transcripts are expected. If weights are uncached, it verifies the transcription warning/fallback instead of downloading anything. It prints the primary upload's job ID and cut count. Meaningful speech recognition needs a real spoken video and cached model weights or a first-run download.

## Later: hosting away from the Mac

The current ship gate is Upload on the existing Mac tunnel. Restarting the running server and tunnel remains an operator task. A later Dockerfile could package Python, ffmpeg/ffprobe and the existing Python dependencies, replace the Mac-specific executable paths in `app.py`, run Uvicorn on the host's assigned port, and mount persistent storage at `data/` for clips and model weights. A container host such as Fly.io or Railway would need CPU/RAM for local Whisper and enough request time for uploads and background jobs. Keep a single worker while jobs live in memory. Hobby Vercel is outside this hosting plan. No container, hosting service or deployment is configured here.

## Ephemeral data and limits

`data/` is gitignored. Original files and extracted audio live in `data/jobs/<id>/`; clips live in `data/media/<id>/`; sample videos and model weights are also under `data/`. There is no automatic deletion. After stopping the server, old job/sample folders can be removed manually when no longer needed. Restarting drops job metadata, so old cards cannot be restored, though their files remain until you remove them. There is no authentication, payment, long-term storage, TikTok support, CapCut export or deployment configuration.
