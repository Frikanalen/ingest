import shutil
import subprocess
from pathlib import Path

import pytest

from app.media.loudness.measure import loudness_command, measure_loudness, parse_loudness

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _clip(path: Path, audio: str) -> Path:
    """A short clip with a video track and the given lavfi audio source."""
    subprocess.run(
        [
            *("ffmpeg", "-y", "-loglevel", "error"),
            *("-f", "lavfi", "-i", audio),
            *("-f", "lavfi", "-i", "smptebars=size=320x240:rate=25"),
            *("-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"),
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def tone_clip(tmp_path_factory) -> Path:
    return _clip(tmp_path_factory.mktemp("loudness") / "tone.mp4", "sine=frequency=1000")


@pytest.fixture(scope="module")
def silent_clip(tmp_path_factory) -> Path:
    return _clip(tmp_path_factory.mktemp("loudness") / "silence.mp4", "anullsrc=r=48000:cl=stereo")


def test_measurement_does_not_decode_the_video_it_is_not_measuring():
    """A loudness pass that decodes video costs about as much as a rendition,
    for frames it throws away."""
    command = loudness_command(Path("/uploads/some file.mov"))

    assert " -vn " in command
    assert '-i "/uploads/some file.mov"' in command


@pytest.mark.asyncio
async def test_measures_a_real_file(tone_clip):
    loudness = await measure_loudness(tone_clip)

    assert loudness is not None
    # A full-scale sine at ffmpeg's default amplitude lands here; the point
    # is that we read loudnorm's numbers rather than that they are exact.
    assert -25 < loudness.integrated_lufs < -15
    assert loudness.truepeak_lufs is not None


@pytest.mark.asyncio
async def test_silence_is_not_a_measurement(silent_clip):
    """Silence measures as -inf, which is neither a number django-api can
    store nor a gain any encode should be asked to apply."""
    assert await measure_loudness(silent_clip) is None


@pytest.mark.asyncio
async def test_an_unreadable_file_measures_as_nothing_rather_than_raising(tmp_path):
    """A missing measurement must not fail an otherwise good ingest."""
    not_media = tmp_path / "not-media.txt"
    not_media.write_text("hello")

    assert await measure_loudness(not_media) is None


def test_reads_the_last_report_in_the_stream():
    """ffmpeg says plenty before loudnorm does, and the braces in a filter
    graph it echoes back are not the report."""
    stderr = """
    [Parsed_loudnorm_0 @ 0x7f8] something with {braces} in it
    {
        "input_i" : "-27.85",
        "input_tp" : "-9.61",
        "input_lra" : "5.20",
        "input_thresh" : "-38.20",
        "output_i" : "-16.00",
        "target_offset" : "0.15"
    }
    """

    loudness = parse_loudness(stderr)

    assert loudness is not None
    assert loudness.integrated_lufs == -27.85
    assert loudness.truepeak_lufs == -9.61
    assert loudness.loudness_range == 5.20
    assert loudness.threshold_lufs == -38.20
    assert loudness.target_offset == 0.15


def test_a_report_that_is_not_there_is_not_a_measurement():
    assert parse_loudness("ffmpeg version 7.1\nConversion failed!\n") is None
