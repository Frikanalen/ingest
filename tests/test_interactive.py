import logging
import shutil
from unittest.mock import AsyncMock

import pytest

import app.loudness.get_loudness
from app.ffprobe import do_probe
from app.make_secondaries import make_secondaries


@pytest.fixture(autouse=True)
def set_log_level():
    logging.getLogger("").setLevel(logging.WARNING)


@pytest.mark.asyncio
async def test_generate(color_bars_video):
    expected_command_line_substring = f"/tmp/large_thumb/{color_bars_video.stem}.jpg"

    runner = AsyncMock()
    metadata = await do_probe(color_bars_video)

    mock_django_api = AsyncMock()

    await make_secondaries(0, color_bars_video, runner=runner, django_api=mock_django_api, metadata=metadata)

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
