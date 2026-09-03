"""Carrying out a plan against a real archive.

The interesting behaviour is ordering and laziness: a rebuild takes the stale
directory out before it publishes over the path, and a plan that needs nothing
from the original must never pull it off the archive.
"""

import logging
import shutil
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.archive_store import LocalArchiveStore
from app.archive_store.base import TRASH_DIR
from app.backfill.actions import ProduceFormat, RefreshMetadata
from app.backfill.apply import Applier, SourceUnavailable
from app.backfill.chores import Plan

VIDEO_ID = "12345"
ORIGINAL = PurePosixPath(f"{VIDEO_ID}/original/source.mp4")
DASH_DIR = PurePosixPath(f"{VIDEO_ID}/dash")


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def work_dir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
def django_api() -> AsyncMock:
    return AsyncMock()


@pytest_asyncio.fixture
async def applier(archive_root, django_api, work_dir):
    async with LocalArchiveStore(archive_root).open() as archive:
        yield Applier(archive=archive, django_api=django_api, work_dir=work_dir)


def place(archive_root: Path, destination: PurePosixPath, payload: bytes = b"media") -> Path:
    target = archive_root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def plan_of(*actions) -> Plan:
    return Plan(video_id=VIDEO_ID, actions=tuple(actions))


@pytest.mark.asyncio
async def test_a_plan_with_nothing_to_do_fetches_nothing(applier, django_api):
    """There is no original in this archive at all.

    Which is the point: a video with nothing registered plans no actions, and
    a run over the catalogue must not turn every one of those into a fetch
    that then fails to find a source.
    """
    await applier.apply(plan_of())

    django_api.set_video_duration.assert_not_awaited()


@pytest.mark.asyncio
async def test_refreshing_records_duration_and_framerate(applier, archive_root, django_api, color_bars_video):
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())

    await applier.apply(plan_of(RefreshMetadata(fields=("duration", "framerate"), original_file_id=7)))

    django_api.set_video_duration.assert_awaited_once()
    # The fixture is generated at 25fps, and the field is thousandths.
    django_api.set_video_framerate.assert_awaited_once_with(VIDEO_ID, 25000)


@pytest.mark.asyncio
async def test_a_silent_source_records_no_loudness(applier, archive_root, django_api, color_bars_video, caplog):
    """A measurement of nothing would be worse than the absence of one.

    Said out loud, though: the column is left as it was found, which is
    indistinguishable from never having measured, so the one round trip that
    established there is nothing to measure has to leave a trace of itself.
    """
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())

    with caplog.at_level(logging.WARNING, logger="app.backfill.apply"):
        await applier.apply(plan_of(RefreshMetadata(fields=("loudness",), original_file_id=7)))

    django_api.set_video_file_loudness.assert_not_awaited()
    assert any("no measurable loudness" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_a_missing_original_is_refused_rather_than_guessed(applier, archive_root):
    with pytest.raises(SourceUnavailable):
        await applier.apply(plan_of(RefreshMetadata(fields=("duration",), original_file_id=7)))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
@pytest.mark.asyncio
async def test_rebuilding_a_format_swaps_the_directory(applier, archive_root, django_api, color_bars_video):
    """The old revision's files must not survive beside the new ones."""
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())
    place(archive_root, PurePosixPath(f"{VIDEO_ID}/large_thumb/leftover-from-v1.jpg"))

    await applier.apply(
        plan_of(
            ProduceFormat(
                file_format=VideoFileVariantEnum.LARGE_THUMB,
                from_revision=0,
                to_revision=1,
                replacing=PurePosixPath(f"{VIDEO_ID}/large_thumb"),
            )
        )
    )

    survivors = sorted(p.name for p in (archive_root / VIDEO_ID / "large_thumb").iterdir())
    assert survivors == ["source.jpg"]
    assert list((archive_root / TRASH_DIR).rglob("leftover-from-v1.jpg"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
@pytest.mark.asyncio
async def test_producing_a_missing_format_registers_it(applier, archive_root, django_api, color_bars_video):
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())

    await applier.apply(plan_of(ProduceFormat(file_format=VideoFileVariantEnum.MED_THUMB, to_revision=1)))

    assert (archive_root / VIDEO_ID / "med_thumb" / "source.jpg").exists()
    [call] = django_api.create_video_file.call_args_list
    assert call.kwargs["profile_revision"] == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
@pytest.mark.asyncio
async def test_producing_over_output_nothing_claims_replaces_it(applier, archive_root, django_api, color_bars_video):
    """put() refuses to overwrite, so a rebuild into an occupied directory has
    to swap it. Without `replacing` this raises FileAlreadyArchived, and would
    do so again on every retry."""
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())
    place(archive_root, PurePosixPath(f"{VIDEO_ID}/med_thumb/source.jpg"), b"an earlier, unregistered thumbnail")

    await applier.apply(
        plan_of(
            ProduceFormat(
                file_format=VideoFileVariantEnum.MED_THUMB,
                to_revision=1,
                replacing=PurePosixPath(f"{VIDEO_ID}/med_thumb"),
            )
        )
    )

    rebuilt = archive_root / VIDEO_ID / "med_thumb" / "source.jpg"
    assert rebuilt.read_bytes() != b"an earlier, unregistered thumbnail"
    assert list((archive_root / TRASH_DIR).rglob("source.jpg"))
