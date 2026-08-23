"""Carrying out a plan against a real archive.

The interesting behaviour is ordering and laziness: the source is fetched from
where the plan will have put it, not from where it started, and a plan that
only tidies directories must never fetch it at all.
"""

import shutil
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.archive_store import LocalArchiveStore
from app.archive_store.base import TRASH_DIR
from app.backfill.actions import MovePath, ProduceFormat, RefreshMetadata, RetagFile, TrashPath, UnregisterFile
from app.backfill.apply import Applier, SourceUnavailable
from app.backfill.chores import Plan
from app.django_client.service import FormatEnum

VIDEO_ID = "12345"
ORIGINAL = PurePosixPath(f"{VIDEO_ID}/original/source.mp4")
BROADCAST = PurePosixPath(f"{VIDEO_ID}/broadcast/source.mp4")


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
async def test_trashing_takes_a_directory_out_of_the_published_tree(applier, archive_root):
    place(archive_root, BROADCAST)

    await applier.apply(plan_of(TrashPath(path=PurePosixPath(f"{VIDEO_ID}/broadcast"), reason="superseded")))

    assert not (archive_root / f"{VIDEO_ID}/broadcast").exists()
    assert list((archive_root / TRASH_DIR).rglob("source.mp4"))


@pytest.mark.asyncio
async def test_moving_relocates_the_media(applier, archive_root):
    place(archive_root, BROADCAST)

    await applier.apply(plan_of(MovePath(source=BROADCAST, destination=ORIGINAL)))

    assert (archive_root / ORIGINAL).read_bytes() == b"media"
    assert not (archive_root / BROADCAST).exists()


@pytest.mark.asyncio
async def test_retagging_updates_the_row_rather_than_creating_one(applier, archive_root, django_api):
    await applier.apply(plan_of(RetagFile(file_id=7, variant=FormatEnum.ORIGINAL, filename=ORIGINAL)))

    django_api.retag_video_file.assert_awaited_once_with(7, FormatEnum.ORIGINAL, str(ORIGINAL))
    django_api.create_video_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_unregistering_drops_the_named_row(applier, django_api):
    await applier.apply(plan_of(UnregisterFile(file_id=7, reason="its file has been trashed")))

    django_api.delete_video_file.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_a_directory_only_plan_never_fetches_the_original(applier, archive_root):
    """There is no original to fetch, and the plan must still succeed."""
    place(archive_root, BROADCAST)

    await applier.apply(plan_of(MovePath(source=BROADCAST, destination=ORIGINAL)))

    assert (archive_root / ORIGINAL).exists()


@pytest.mark.asyncio
async def test_the_source_is_fetched_from_where_the_plan_moved_it(applier, archive_root, color_bars_video):
    """The rename happens first, so fetching up front would look in the wrong
    directory. This is the whole reason the fetch is lazy."""
    place(archive_root, BROADCAST, color_bars_video.read_bytes())

    await applier.apply(
        plan_of(
            MovePath(source=BROADCAST, destination=ORIGINAL),
            RefreshMetadata(fields=("duration",), original_file_id=7),
        )
    )

    assert (archive_root / ORIGINAL).exists()


@pytest.mark.asyncio
async def test_refreshing_records_duration_and_framerate(applier, archive_root, django_api, color_bars_video):
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())

    await applier.apply(plan_of(RefreshMetadata(fields=("duration", "framerate"), original_file_id=7)))

    django_api.set_video_duration.assert_awaited_once()
    # The fixture is generated at 25fps, and the field is thousandths.
    django_api.set_video_framerate.assert_awaited_once_with(VIDEO_ID, 25000)


@pytest.mark.asyncio
async def test_a_silent_source_records_no_loudness(applier, archive_root, django_api, color_bars_video):
    """A measurement of nothing would be worse than the absence of one."""
    place(archive_root, ORIGINAL, color_bars_video.read_bytes())

    await applier.apply(plan_of(RefreshMetadata(fields=("loudness",), original_file_id=7)))

    django_api.set_video_file_loudness.assert_not_awaited()


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
                file_format=FormatEnum.LARGE_THUMB,
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

    await applier.apply(plan_of(ProduceFormat(file_format=FormatEnum.MED_THUMB, to_revision=1)))

    assert (archive_root / VIDEO_ID / "med_thumb" / "source.jpg").exists()
    [call] = django_api.create_video_file.call_args_list
    assert call.kwargs["profile_revision"] == 1
