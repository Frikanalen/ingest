import pytest

from app.api.hooks.metadata import MetadataExtractor


@pytest.mark.asyncio
async def test_get_metadata(color_bars_video):
    extractor = MetadataExtractor()
    data = await extractor.do_probe(color_bars_video)
    assert data.format.duration == "1.000000"
    assert data.streams[0].codec_name == "h264"
    assert data.streams[0].width == 1280
    assert data.streams[0].height == 720
