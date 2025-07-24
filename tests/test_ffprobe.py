import pytest

from app.ffprobe import do_probe


@pytest.mark.anyio
async def test_do_probe(color_bars_video):
    data = await do_probe(color_bars_video)
    assert data.format.duration == "1.000000"
    assert data.streams[0].codec_name == "h264"
    assert data.streams[0].width == 1280
    assert data.streams[0].height == 720
