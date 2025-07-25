import logging
import shutil
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import FormatEnum

import app.loudness.get_loudness
from app.converter import Converter
from app.ffprobe import do_probe


@pytest.fixture(autouse=True)
def set_log_level():
    logging.getLogger("").setLevel(logging.INFO)


@pytest.mark.asyncio
async def test_generate(color_bars_video):
    expected_command_line_substring = f"/tmp/large_thumb/{color_bars_video.stem}.jpg"

    runner = AsyncMock()
    mock_django_api = AsyncMock()

    metadata = await do_probe(color_bars_video)
    converter = Converter(runner=runner, django_api=mock_django_api)
    await converter.process_format(color_bars_video, FormatEnum.LARGE_THUMB, metadata, 0)

    runner.run.assert_called_once()
    assert expected_command_line_substring in runner.run.call_args[0][0]


def test_get_loudness():
    if not can_get_loudness():
        pytest.skip("bs1770gain missing")

    # sine.wav was generated using sox -b 16 -n sine.wav synth 3 sine 300-3300
    # white.png was generated using convert xc:white white.png
    assert app.loudness.get_loudness.get_loudness("../tests/data/sine.wav") == {
        "integrated_lufs": -2.2,
        "truepeak_lufs": 0.54,
    }
    assert app.loudness.get_loudness.get_loudness("../tests/data/white.jpg") is None


def can_get_loudness():
    return shutil.which("bs1770gain") is not None
