"""Prepared WAV input reaches Whisper without decoding through PyAV again."""
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from config import ROOT
from whisper_worker import load_pcm_audio


class PreparedAudioTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "data").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "data")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "speech.wav"

    def write_audio(self, pcm, channels=1, width=2, rate=16000):
        with wave.open(str(self.path), "wb") as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(width)
            audio.setframerate(rate)
            audio.writeframes(pcm)

    def test_pcm_becomes_normalised_float32_samples(self):
        self.write_audio(struct.pack("<hhh", -32768, 0, 32767))
        samples = load_pcm_audio(self.path)
        self.assertEqual(samples.dtype, np.float32)
        self.assertEqual(samples.shape, (3,))
        np.testing.assert_array_equal(samples, [-1.0, 0.0, 32767 / 32768])

    def test_unexpected_format_is_rejected(self):
        for options in ({"channels": 2}, {"width": 1}, {"rate": 44100}):
            with self.subTest(options=options):
                self.write_audio(bytes(16), **options)
                with self.assertRaises(ValueError):
                    load_pcm_audio(self.path)

    def test_empty_audio_is_rejected(self):
        self.write_audio(b"")
        with self.assertRaises(ValueError):
            load_pcm_audio(self.path)

    def test_duration_limit_is_enforced(self):
        self.write_audio(bytes(32002))
        with patch("whisper_worker.MAX_SECONDS", 1), self.assertRaises(ValueError):
            load_pcm_audio(self.path)

    def test_truncated_audio_is_rejected(self):
        self.write_audio(bytes(16))
        self.path.write_bytes(self.path.read_bytes()[:-2])
        with self.assertRaises(ValueError):
            load_pcm_audio(self.path)
