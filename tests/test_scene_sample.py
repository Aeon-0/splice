"""Offline ffmpeg regression for Luke's missed Claude cut; no Whisper/network."""
import time
import unittest
from unittest.mock import patch

from config import ROOT
from pipeline import cut_boundaries, probe_video


class MissedCutSampleTests(unittest.TestCase):
    def test_claude_prompt_beat_is_separate_without_fragmenting_sample(self):
        sample = ROOT / "testdata" / "luke-missed-cut-sample.mp4"
        self.assertTrue(sample.is_file(), "The committed missed-cut fixture is required.")
        deadline = time.monotonic() + 60
        duration, _ = probe_video(sample, sample.parent, deadline, ".mp4")
        # Probed at 30fps, 13.162857s. Frame 139 (4.633s) -> 140 (4.667s)
        # jumps between the similar laptop shots for "Open Claude" and "Type
        # this prompt"; the caption changes shortly after the camera cut.
        # ffmpeg scene score: 0.263470. Use 4.60–4.73s (about +/- two frames),
        # not an exact floating-point timestamp, across ffmpeg builds.
        window = (4.60, 4.73)
        with patch("pipeline.SCENE_THRESHOLD", 0.30):
            old = cut_boundaries(sample, duration, sample.parent, deadline)
        self.assertFalse(any(window[0] <= point <= window[1] for point in old))
        boundaries = cut_boundaries(sample, duration, sample.parent, deadline)
        self.assertEqual(sum(window[0] <= point <= window[1] for point in boundaries), 1)
        self.assertEqual(len(boundaries) - 1, 9, boundaries)
        self.assertTrue(all(any(abs(point - existing) < 0.04 for point in boundaries) for existing in old))
        self.assertGreaterEqual(min(end - start for start, end in zip(boundaries, boundaries[1:])), 0.85)
        self.assertEqual(boundaries[0], 0)
        self.assertAlmostEqual(boundaries[-1], duration)


if __name__ == "__main__":
    unittest.main()
