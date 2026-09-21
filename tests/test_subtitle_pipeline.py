import io
import os
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from subtitle_pipeline import (
    format_srt_timestamp,
    segment_times_seconds,
    transcribe_audio_with_model,
)


class SegmentTimesTests(unittest.TestCase):
    def test_snaps_segment_start_to_first_word_start(self) -> None:
        segment = {
            "start": 10.0,
            "end": 14.0,
            "words": [
                {"word": " hello", "start": 11.25, "end": 11.7},
                {"word": " world", "start": 11.8, "end": 12.2},
            ],
        }

        start_seconds, end_seconds = segment_times_seconds(
            segment,
            snap_start_to_first_word=True,
        )

        self.assertEqual(format_srt_timestamp(start_seconds), "00:00:11,250")
        self.assertEqual(format_srt_timestamp(end_seconds), "00:00:14,000")

    def test_keeps_segment_start_when_snap_is_disabled(self) -> None:
        segment = {
            "start": 10.0,
            "end": 14.0,
            "words": [{"word": " hello", "start": 11.25, "end": 11.7}],
        }

        start_seconds, end_seconds = segment_times_seconds(
            segment,
            snap_start_to_first_word=False,
        )

        self.assertEqual(format_srt_timestamp(start_seconds), "00:00:10,000")
        self.assertEqual(format_srt_timestamp(end_seconds), "00:00:14,000")

    def test_does_not_move_start_earlier_than_segment_start(self) -> None:
        segment = {
            "start": 10.0,
            "end": 14.0,
            "words": [{"word": " hello", "start": 9.8, "end": 10.3}],
        }

        start_seconds, _ = segment_times_seconds(
            segment,
            snap_start_to_first_word=True,
        )

        self.assertEqual(format_srt_timestamp(start_seconds), "00:00:10,000")


class WhisperSettingsTests(unittest.TestCase):
    def test_transcription_reuses_resolved_word_timestamp_setting(self) -> None:
        class FakeModel:
            calls: list[dict] = []

            def transcribe(self, _audio_path: str, **options: object) -> dict:
                self.calls.append(options)
                return {"segments": [{"start": 0, "end": 1, "text": "hello"}]}

        model = FakeModel()
        stderr = io.StringIO()

        with patch.dict(os.environ, {"WHISPER_WORD_TIMESTAMPS": "true"}), redirect_stderr(stderr):
            for _ in range(2):
                transcribe_audio_with_model(
                    model,
                    Path("audio.wav"),
                    use_fp16=False,
                    word_timestamps=False,
                )

        self.assertEqual([call["word_timestamps"] for call in model.calls], [False, False])
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
