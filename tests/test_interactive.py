import logging
import shutil
from unittest.mock import AsyncMock

import pytest

import app.loudness.get_loudness
from app import interactive
from app.ffprobe import do_probe


class MockRunner:
    def __init__(self):
        self.mocked_commands: list[str] = []

    def run(self, cmd: str, **kwargs):
        print(f"Running: {cmd}")
        self.mocked_commands.append(cmd)


@pytest.fixture(autouse=True)
def set_log_level():
    logging.getLogger("").setLevel(logging.WARNING)


@pytest.mark.anyio
async def test_generate(color_bars_video):
    tests = (
        (color_bars_video, f"/tmp/large_thumb/{color_bars_video.stem}.jpg"),
        (color_bars_video, f"/tmp/large_thumb/{color_bars_video.stem}.jpg"),
    )

    for input_filename, expected_command_line_substring in tests:
        runner = AsyncMock()
        metadata = await do_probe(color_bars_video)

        mock_django_api = AsyncMock()

        async def mock_register(video_id, filename):
            print(f"Registering file name {filename} for video {video_id}")

        await interactive.generate_videos(
            0, input_filename, runner=runner, django_api=mock_django_api, register=mock_register, metadata=metadata
        )

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


# @pytest.mark.anyio
# async def test_generate_wrong_format(tmp_path):
#     with pytest.raises(AttributeError):
#         bogus_file = tmp_path / "c.d"
#         bogus_file.touch()
#         await interactive.generate_videos(0, bogus_file)


def can_get_loudness():
    return shutil.which("bs1770gain") is not None
