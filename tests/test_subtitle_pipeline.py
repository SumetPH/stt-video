import unittest

from subtitle_pipeline import format_srt_timestamp, segment_times_seconds


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


if __name__ == "__main__":
    unittest.main()
