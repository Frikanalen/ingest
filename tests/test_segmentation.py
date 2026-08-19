"""Choosing a segment length the manifest can tell the truth about."""

from fractions import Fraction

import pytest

from app.media.ffprobe_schema import FfprobeOutput
from app.media.segmentation import FALLBACK_FRAME_RATE, TARGET_SEGMENT_S, segmentation_for


def probe(**stream) -> FfprobeOutput:
    return FfprobeOutput.model_validate(
        {"streams": [{"index": 0, "codec_tag_string": "avc1", "codec_tag": "0x1", "codec_type": "video", **stream}]}
    )


@pytest.mark.parametrize(
    "frame_rate,expected_gop,expected_duration",
    [
        ("25/1", 150, "6.000000"),
        ("50/1", 300, "6.000000"),
        ("24/1", 144, "6.000000"),
        # The broadcast rates, where a whole number of seconds is not a whole
        # number of frames and 6.000s segments simply do not exist.
        ("60000/1001", 360, "6.006000"),
        ("30000/1001", 180, "6.006000"),
        ("24000/1001", 144, "6.006000"),
    ],
)
def test_segment_length_is_a_whole_number_of_frames(frame_rate, expected_gop, expected_duration):
    segmentation = segmentation_for(probe(avg_frame_rate=frame_rate))

    assert segmentation.gop_frames == expected_gop
    assert segmentation.segment_duration_arg == expected_duration


@pytest.mark.parametrize("frame_rate", ["25/1", "60000/1001", "30000/1001"])
def test_the_declared_length_is_exactly_the_gop_length(frame_rate):
    """This is the whole point: ffmpeg writes the declared length into the
    manifest, and players locate a segment by dividing by it. Any gap between
    the two accumulates, so a seek lands further out the later it is."""
    segmentation = segmentation_for(probe(avg_frame_rate=frame_rate))

    assert segmentation.segment_duration == Fraction(segmentation.gop_frames) / segmentation.frame_rate
    assert float(segmentation.segment_duration_arg) == pytest.approx(float(segmentation.segment_duration))


def test_a_segment_stays_near_the_target_length():
    for rate in ("25/1", "50/1", "60000/1001", "30000/1001", "24000/1001"):
        assert abs(float(segmentation_for(probe(avg_frame_rate=rate)).segment_duration) - TARGET_SEGMENT_S) < 0.5


def test_falls_back_to_r_frame_rate_when_the_average_is_missing():
    assert segmentation_for(probe(r_frame_rate="60000/1001")).frame_rate == Fraction(60000, 1001)


@pytest.mark.parametrize("nonsense", ["0/0", "0/1", "", "not-a-rate", "100000/1"])
def test_ignores_a_frame_rate_that_cannot_be_true(nonsense):
    """Encoding is CFR, so believing a malformed file's 100000fps would try to
    produce a hundred thousand frames a second."""
    assert segmentation_for(probe(avg_frame_rate=nonsense)).frame_rate == FALLBACK_FRAME_RATE


def test_falls_back_when_there_is_no_video_stream_at_all():
    assert segmentation_for(FfprobeOutput.model_validate({"streams": []})).frame_rate == FALLBACK_FRAME_RATE
    assert segmentation_for(FfprobeOutput.model_validate({})).frame_rate == FALLBACK_FRAME_RATE
