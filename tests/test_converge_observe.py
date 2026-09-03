"""Reading one video's catalogue rows and its archived directories.

What a worker does when it claims a job. Mostly bookkeeping; the part worth
being careful about is that a revision nothing recorded reads as stale rather
than as current, since that is what decides whether a format is rebuilt.
"""

from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import VideoFileVariantEnum
from frikanalen_django_api_client.types import UNSET

from app.archive_store import LocalArchiveStore
from app.converge.observe import Observer
from app.formats import UNTRACKED_REVISION

VIDEO_ID = "12345"


def video_row(video_id: int, duration="00:10:00", framerate=25000):
    return SimpleNamespace(id=video_id, duration=duration, framerate=framerate)


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


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def django_api():
    api = AsyncMock()
    api.get_video.return_value = video_row(int(VIDEO_ID))
    api.get_files_for_video.return_value = SimpleNamespace(
        count=1, results=[file_row(1, int(VIDEO_ID), "original", f"{VIDEO_ID}/original/source.mp4")]
    )
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
async def test_observing_reads_every_directory_and_its_contents(observer, archive_root):
    place(archive_root, f"{VIDEO_ID}/original/source.mp4")
    place(archive_root, f"{VIDEO_ID}/dash/manifest.mpd")
    place(archive_root, f"{VIDEO_ID}/dash/manifest-stream0.mp4")

    state = await observer.observe_one(VIDEO_ID)

    assert set(state.directories) == {"original", "dash"}
    assert len(state.contents_of("dash")) == 2
    assert state.contents_of("original")[0].path == PurePosixPath(f"{VIDEO_ID}/original/source.mp4")


@pytest.mark.asyncio
async def test_observing_carries_the_videos_own_fields(observer):
    state = await observer.observe_one(VIDEO_ID)

    assert state.duration == "00:10:00"
    assert state.framerate == 25000


@pytest.mark.asyncio
async def test_a_video_with_nothing_archived_observes_as_empty_rather_than_unread(observer):
    """Empty, not None. A worker did look, and found nothing -- which is what
    lets a chore say a registered format is missing from the archive."""
    state = await observer.observe_one(VIDEO_ID)

    assert state.directories == {}
    assert state.archive_was_read


@pytest.mark.asyncio
async def test_a_file_with_no_recorded_revision_reads_as_untracked(observer):
    state = await observer.observe_one(VIDEO_ID)

    [original] = state.files
    assert original.profile_revision == UNTRACKED_REVISION


@pytest.mark.asyncio
async def test_a_recorded_revision_is_read_back(observer, django_api):
    django_api.get_files_for_video.return_value = SimpleNamespace(
        count=1, results=[file_row(2, int(VIDEO_ID), "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=2)]
    )

    state = await observer.observe_one(VIDEO_ID)

    assert state.files[0].profile_revision == 2


@pytest.mark.asyncio
async def test_a_row_that_omits_the_revision_reads_as_untracked(observer, django_api):
    """Nothing recorded must read as stale rather than as current."""
    django_api.get_files_for_video.return_value = SimpleNamespace(
        count=1, results=[file_row(2, int(VIDEO_ID), "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=UNSET)]
    )

    state = await observer.observe_one(VIDEO_ID)

    assert state.files[0].profile_revision == UNTRACKED_REVISION
