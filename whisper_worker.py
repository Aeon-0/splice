"""Private offline worker. A single base model is reused until exit or timeout."""
import json
import sys
import wave
from pathlib import Path

from config import MAX_SECONDS
from transcript import slice_transcript


def load_pcm_audio(path):
    """Read only the bounded mono PCM WAV already produced by our ffmpeg step."""
    import numpy as np

    with wave.open(str(path), "rb") as audio:
        frames = audio.getnframes()
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getframerate() != 16000 or not 0 < frames <= MAX_SECONDS * 16000):
            raise ValueError("Invalid prepared audio.")
        pcm = audio.readframes(frames)
    if len(pcm) != frames * 2:
        raise ValueError("Incomplete prepared audio.")
    # Array input bypasses PyAV decoding; ffmpeg has already decoded/resampled.
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def main():
    model = None
    model_dir = Path(sys.argv[1]).resolve()
    uploads = Path(sys.argv[2]).resolve()
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line)
            audio = Path(request["audio"]).resolve()
            if not audio.is_relative_to(uploads) or audio.name != "speech.wav":
                raise ValueError
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(str(model_dir / "base"), device="cpu", compute_type="int8", cpu_threads=2,
                                     num_workers=1, local_files_only=True)
            segments, _ = model.transcribe(load_pcm_audio(audio), beam_size=5, word_timestamps=True, vad_filter=True)
            result = {"ok": True, "transcripts": slice_transcript(segments, request["cuts"])}
        except Exception:
            result = {"ok": False}
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
