"""Reading the catalogue and the archive.

Most of this is bookkeeping, with one exception worth being careful about: a
catalogue read that came up short must not be handed on as though it were
complete, because the chore that reclaims media reads absence as permission.
"""

from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import VideoFileVariantEnum
from frikanalen_django_api_client.types import UNSET

from app.archive_store import LocalArchiveStore
from app.backfill.observe import IncompleteSnapshot, Observer
from app.formats import UNTRACKED_REVISION

VIDEO_ID = "12345"


def video_row(video_id: int, duration="00:10:00", framerate=25000, proper_import=True):
    return SimpleNamespace(id=video_id, duration=duration, framerate=framerate, proper_import=proper_import)


def file_row(file_id, video, variant, filename, revision=UNTRACKED_REVISION, lufs=-23.0):
    """A videofile as the client hands it over.

    `profile_revision` is NOT NULL with a zero default, so a real row always
    carries one; pass UNSET to model a response that left it out.
    """
    return SimpleNamespace(
        id=file_id,
        video=video,
        variant=VideoFileVariantEnum(variant),
        filename=filename,
        integrated_lufs=lufs,
        profile_revision=revision,
        additional_properties={},
    )


def pager(rows, count=None):
    """A paginated endpoint that hands back `rows`, honouring limit/offset.

    The video list filters on `proper_import` the way django-api does, so a
    caller that asks for only one half of the catalogue gets only one half
    here too. A mock that ignored the filter would answer every question
    correctly and hide the one bug this endpoint has ever had.
    """

    async def fetch(limit: int, offset: int, *, proper_import=None):
        matching = rows if proper_import is None else [row for row in rows if row.proper_import is proper_import]
        total = len(matching) if count is None else count
        return SimpleNamespace(count=total, results=matching[offset : offset + limit])

    return fetch


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def django_api():
    api = AsyncMock()
    api.list_videos_page = pager([video_row(12345)])
    api.list_video_files_page = pager([file_row(1, 12345, "original", f"{VIDEO_ID}/original/source.mp4")])
    return api


@pytest_asyncio.fixture
async def observer(archive_root, django_api):
    async with LocalArchiveStore(archive_root).open() as archive:
        yield Observer(archive, django_api)


def place(archive_root, path: str, payload=b"media"):
    target = archive_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


@pytest.mark.asyncio
async def test_snapshot_groups_files_by_video(observer, django_api):
    django_api.list_video_files_page = pager(
        [
            file_row(1, 12345, "original", f"{VIDEO_ID}/original/source.mp4"),
            file_row(2, 12345, "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=1),
            file_row(3, 999, "original", "999/original/other.mp4"),
        ]
    )

    snapshot = await observer.snapshot()

    assert {row.variant for row in snapshot.files_for(VIDEO_ID)} == {
        VideoFileVariantEnum.ORIGINAL,
        VideoFileVariantEnum.DASH,
    }
    assert len(snapshot.files_for("999")) == 1
    assert snapshot.files_for("nobody") == ()


@pytest.mark.asyncio
async def test_snapshot_pages_through_everything(observer, django_api):
    django_api.list_videos_page = pager([video_row(i) for i in range(1, 1201)])

    snapshot = await observer.snapshot()

    assert len(snapshot.videos) == 1200


@pytest.mark.asyncio
async def test_a_short_read_is_refused_rather_than_returned(observer, django_api):
    """Reclaiming media reads absence as permission, so a partial catalogue
    must never be mistaken for a complete one."""
    django_api.list_videos_page = pager([video_row(1)], count=900)

    with pytest.raises(IncompleteSnapshot):
        await observer.snapshot()


@pytest.mark.asyncio
async def test_a_file_with_no_recorded_revision_reads_as_untracked(observer):
    snapshot = await observer.snapshot()

    [original] = snapshot.files_for(VIDEO_ID)
    assert original.profile_revision == UNTRACKED_REVISION


@pytest.mark.asyncio
async def test_a_recorded_revision_is_read_back(observer, django_api):
    django_api.list_video_files_page = pager([file_row(2, 12345, "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=2)])

    snapshot = await observer.snapshot()

    assert snapshot.files_for(VIDEO_ID)[0].profile_revision == 2


@pytest.mark.asyncio
async def test_a_row_that_omits_the_revision_reads_as_untracked(observer, django_api):
    """Nothing recorded must read as stale rather than as current."""
    django_api.list_video_files_page = pager(
        [file_row(2, 12345, "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=UNSET)]
    )

    snapshot = await observer.snapshot()

    assert snapshot.files_for(VIDEO_ID)[0].profile_revision == UNTRACKED_REVISION


@pytest.mark.asyncio
async def test_observing_reads_every_directory_and_its_contents(observer, archive_root):
    place(archive_root, f"{VIDEO_ID}/original/source.mp4")
    place(archive_root, f"{VIDEO_ID}/dash/manifest.mpd")
    place(archive_root, f"{VIDEO_ID}/dash/manifest-stream0.mp4")

    state = await observer.observe(VIDEO_ID, await observer.snapshot())

    assert set(state.directories) == {"original", "dash"}
    assert len(state.contents_of("dash")) == 2
    assert state.contents_of("original")[0].path == PurePosixPath(f"{VIDEO_ID}/original/source.mp4")


@pytest.mark.asyncio
async def test_observing_carries_the_videos_own_fields(observer):
    state = await observer.observe(VIDEO_ID, await observer.snapshot())

    assert state.in_catalogue
    assert state.duration == "00:10:00"
    assert state.framerate == 25000


@pytest.mark.asyncio
async def test_a_directory_the_catalogue_does_not_know_is_not_in_the_catalogue(observer, archive_root):
    place(archive_root, "777/original/orphan.mp4")

    state = await observer.observe("777", await observer.snapshot())

    assert not state.in_catalogue
    assert state.contents_of("original")


@pytest.mark.asyncio
async def test_a_video_with_nothing_archived_observes_as_empty(observer):
    state = await observer.observe(VIDEO_ID, await observer.snapshot())

    assert state.directories == {}
    assert state.in_catalogue


@pytest.mark.asyncio
async def test_the_snapshot_holds_videos_whose_ingest_has_not_finished(observer, django_api):
    """django-api's video list defaults to the public catalogue -- finished
    ingests only. The snapshot has to be wider than that, because gc reads
    absence from it as permission to reclaim a video's archived media."""
    django_api.list_videos_page = pager([video_row(1), video_row(2, proper_import=False)])

    snapshot = await observer.snapshot()

    assert set(snapshot.videos) == {"1", "2"}
    assert "2" in snapshot


@pytest.mark.asyncio
async def test_a_short_read_of_the_unfinished_videos_is_refused_too(observer, django_api):
    """Both passes carry the same weight. A shortfall in the unfinished half
    hides exactly the videos this change exists to protect."""
    django_api.list_videos_page = pager([video_row(1, proper_import=False)], count=900)

    with pytest.raises(IncompleteSnapshot, match="unfinished videos"):
        await observer.snapshot()
