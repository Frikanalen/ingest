from pathlib import Path

import pytest

from app.media.ffprobe_schema import FfprobeOutput
from app.media.produce import SourceMedia
from app.media.self_test import SelfTestFailed, check_ladder, self_test

MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
<AdaptationSet id="0">{video}</AdaptationSet>
<AdaptationSet id="2"><Representation id="6" codecs="mp4a.40.2"/></AdaptationSet>
</Period></MPD>"""

FULL_LADDER = [
    ("av01.0.08M.08", 1920, 1080),
    ("av01.0.05M.08", 1280, 720),
    ("av01.0.04M.08", 960, 540),
    ("av01.0.01M.08", 640, 360),
    ("avc1.64001f", 1280, 720),
    ("avc1.64001e", 640, 360),
]


def manifest(tmp_path: Path, representations) -> Path:
    video = "".join(f'<Representation codecs="{c}" width="{w}" height="{h}"/>' for c, w, h in representations)
    path = tmp_path / "manifest.mpd"
    path.write_text(MPD.format(video=video))
    return path


TAGS = {"codec_tag": "0x0000", "codec_tag_string": "[0][0][0][0]"}


def hd_source() -> SourceMedia:
    metadata = FfprobeOutput.model_validate(
        {
            "streams": [
                {**TAGS, "index": 0, "codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "25/1"},
                {**TAGS, "index": 1, "codec_type": "audio"},
            ]
        }
    )
    return SourceMedia.probed("self-test", Path("clip.mp4"), metadata)


def test_the_whole_ladder_passes(tmp_path):
    check_ladder(manifest(tmp_path, FULL_LADDER), hd_source())


def test_a_missing_rung_fails(tmp_path):
    with pytest.raises(SelfTestFailed):
        check_ladder(manifest(tmp_path, FULL_LADDER[:-1]), hd_source())


def test_a_rung_in_the_wrong_codec_fails(tmp_path):
    """An encoder that silently fell back to another codec is not the ladder."""
    wrong = [("vp09.00.40.08", 1920, 1080), *FULL_LADDER[1:]]
    with pytest.raises(SelfTestFailed):
        check_ladder(manifest(tmp_path, wrong), hd_source())


def test_a_rung_at_the_wrong_size_fails(tmp_path):
    wrong = [("av01.0.08M.08", 1280, 720), *FULL_LADDER[1:]]
    with pytest.raises(SelfTestFailed):
        check_ladder(manifest(tmp_path, wrong), hd_source())


@pytest.mark.asyncio
async def test_the_cpu_encoder_passes_for_real(tmp_path):
    """The whole self-test, end to end: the clip, both formats and the check."""
    await self_test("cpu", tmp_path)


@pytest.mark.asyncio
async def test_an_encoder_that_cannot_start_fails_fast(tmp_path):
    """No Intel GPU here, so QSV cannot open a device -- which is exactly the
    failure a misconfigured GPU node has."""
    with pytest.raises(SelfTestFailed):
        await self_test("qsv", tmp_path, timeout_s=60)
