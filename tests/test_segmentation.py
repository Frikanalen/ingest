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


@pytest.mark.parametrize(
    "average,declared,expected",
    [
        # The one that got out: a 25fps recording whose frame count and
        # duration disagree about the final frame, so the measured average
        # lands 0.001% below the rate the container declares -- enough to put
        # 150 frames at 6.000065s instead of the 6.000000s they really are.
        ("2295400/91817", "25/1", Fraction(25)),
        ("60000/1001", "60000/1001", Fraction(60000, 1001)),
        # Genuinely different rates, not one rate measured twice: believe the
        # measurement, which is what the file actually plays at.
        ("25/1", "50/1", Fraction(25)),
        ("30000/1001", "1000/1", Fraction(30000, 1001)),
    ],
)
def test_a_measured_rate_a_hair_off_the_declared_one_is_the_declared_one(average, declared, expected):
    """avg_frame_rate is frame count over duration, so on a constant-rate file
    it lands near the container's exact ratio rather than on it. Carrying that
    near-miss forward puts a segment length in the manifest that no segment
    has."""
    assert segmentation_for(probe(avg_frame_rate=average, r_frame_rate=declared)).frame_rate == expected


@pytest.mark.parametrize(
    "average,declared",
    [("2295400/91817", "25/1"), ("60000/1001", "60000/1001"), ("25/1", "50/1"), ("1439/60", "24/1")],
)
def test_the_declared_length_is_never_longer_than_the_real_one(average, declared):
    """ffmpeg ends a segment at the first keyframe at or past the declared
    length. Declare a hair too much and it skips that keyframe for the next
    one: segments come out twice as long as the manifest says, and every seek
    lands in the wrong half of the timeline."""
    segmentation = segmentation_for(probe(avg_frame_rate=average, r_frame_rate=declared))

    assert Fraction(segmentation.segment_duration_arg) <= segmentation.segment_duration
    assert segmentation.segment_duration - Fraction(segmentation.segment_duration_arg) < Fraction(1, 1_000_000)


def test_the_frame_rate_is_handed_over_as_an_exact_ratio():
    """A decimal would put the same rounding back that the ratio avoids."""
    assert segmentation_for(probe(avg_frame_rate="60000/1001")).frame_rate_arg == "60000/1001"
    assert segmentation_for(probe(avg_frame_rate="25/1")).frame_rate_arg == "25/1"


@pytest.mark.parametrize("nonsense", ["0/0", "0/1", "", "not-a-rate", "100000/1"])
def test_ignores_a_frame_rate_that_cannot_be_true(nonsense):
    """Encoding is CFR, so believing a malformed file's 100000fps would try to
    produce a hundred thousand frames a second."""
    assert segmentation_for(probe(avg_frame_rate=nonsense)).frame_rate == FALLBACK_FRAME_RATE


def test_falls_back_when_there_is_no_video_stream_at_all():
    assert segmentation_for(FfprobeOutput.model_validate({"streams": []})).frame_rate == FALLBACK_FRAME_RATE
    assert segmentation_for(FfprobeOutput.model_validate({})).frame_rate == FALLBACK_FRAME_RATE
