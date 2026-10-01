from fractions import Fraction

from app.media.ffprobe_schema import FfprobeOutput
from app.media.ladder import LADDER, padded_size, rungs_for


def source(width, height) -> FfprobeOutput:
    stream = {"index": 0, "codec_tag": "0x0000", "codec_tag_string": "[0][0][0][0]", "codec_type": "video"}
    return FfprobeOutput.model_validate({"streams": [{**stream, "width": width, "height": height}]})


def sizes(rungs):
    return [(rung.codec, rung.width, rung.height) for rung in rungs]


def test_a_1080p_source_gets_the_whole_ladder():
    assert sizes(rungs_for(source(1920, 1080), Fraction(25))) == [
        ("av1", 1920, 1080),
        ("av1", 1280, 720),
        ("av1", 960, 540),
        ("av1", 640, 360),
        ("h264", 1280, 720),
        ("h264", 640, 360),
    ]


def test_a_4k_source_gets_a_2160p_rung_on_top():
    rungs = rungs_for(source(3840, 2160), Fraction(25))

    assert sizes(rungs)[:2] == [("av1", 3840, 2160), ("av1", 1920, 1080)]
    assert len(rungs) == len(LADDER)
    assert rungs[0].bitrate_k > rungs[1].bitrate_k


def test_a_1440p_source_gets_its_own_height_on_top():
    """Between 1080p and 4K, the top rung is the source's height, not a
    second 1080p."""
    assert sizes(rungs_for(source(2560, 1440), Fraction(25)))[:2] == [("av1", 2560, 1440), ("av1", 1920, 1080)]


def test_h264_is_a_short_fallback():
    """At most two rungs, topping out at 720p."""
    h264 = [spec for spec in LADDER if spec.codec == "h264"]

    assert len(h264) <= 2
    assert max(spec.height for spec in h264) == 720


def test_nothing_is_upscaled_past_the_source():
    """A 576-line upload must not be blown up to 1080p and charged for it."""
    rungs = rungs_for(source(1024, 576), Fraction(25))

    assert len(rungs) == len(LADDER) - 1, "only the 2160p rung is dropped"

    assert max(rung.height for rung in rungs) == 576
    assert sizes(rungs)[0] == ("av1", 1024, 576)


def test_rung_sizes_follow_the_pad_to_16_9():
    """A 4:3 source is pillarboxed out to 16:9 before scaling, exactly as the
    templates' pad filter does it."""
    assert padded_size(720, 576) == (1024, 576)
    assert padded_size(1920, 800) == (1920, 1080)
    assert sizes(rungs_for(source(720, 576), Fraction(25)))[0] == ("av1", 1024, 576)


def test_every_rung_has_even_dimensions():
    """4:2:0 chroma, and every hardware encoder, need them."""
    for width, height in ((1920, 1080), (1918, 1078), (721, 577), (640, 480)):
        for rung in rungs_for(source(width, height), Fraction(25)):
            assert rung.width % 2 == 0 and rung.height % 2 == 0, (width, height, rung)


def test_high_frame_rates_raise_the_larger_rungs():
    at_25 = {(rung.codec, rung.height): rung.bitrate_k for rung in rungs_for(source(1920, 1080), Fraction(25))}
    at_50 = {(rung.codec, rung.height): rung.bitrate_k for rung in rungs_for(source(1920, 1080), Fraction(50))}

    assert at_50[("av1", 1080)] > at_25[("av1", 1080)]
    assert at_50[("h264", 720)] > at_25[("h264", 720)]
    assert at_50[("av1", 360)] == at_25[("av1", 360)]


def test_every_rung_is_capped_above_its_average():
    for rung in rungs_for(source(1920, 1080), Fraction(25)):
        assert rung.bitrate_k < rung.maxrate_k < rung.bufsize_k
