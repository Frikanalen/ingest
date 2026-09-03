"""Reading the whole catalogue before deciding what to queue.

Most of this is paging, with two things worth being careful about: a read that
came up short must not be handed on as though it were complete, and the states
it produces must say that nobody looked at the archive -- otherwise a chore
reports every registered format as missing from it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum
from frikanalen_django_api_client.types import UNSET

from app.formats import UNTRACKED_REVISION
from fk_queue.catalogue import IncompleteCatalogue, read

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
def django_api():
    api = AsyncMock()
    api.list_videos_page = pager([video_row(int(VIDEO_ID))])
    api.list_video_files_page = pager([file_row(1, int(VIDEO_ID), "original", f"{VIDEO_ID}/original/source.mp4")])
    return api


@pytest.mark.asyncio
async def test_files_are_grouped_onto_their_videos(django_api):
    django_api.list_videos_page = pager([video_row(int(VIDEO_ID)), video_row(999)])
    django_api.list_video_files_page = pager(
        [
            file_row(1, int(VIDEO_ID), "original", f"{VIDEO_ID}/original/source.mp4"),
            file_row(2, int(VIDEO_ID), "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=1),
            file_row(3, 999, "original", "999/original/other.mp4"),
        ]
    )

    catalogue = await read(django_api)

    assert {row.variant for row in catalogue[VIDEO_ID].files} == {
        VideoFileVariantEnum.ORIGINAL,
        VideoFileVariantEnum.DASH,
    }
    assert len(catalogue["999"].files) == 1


@pytest.mark.asyncio
async def test_a_video_with_no_registered_files_is_still_in_the_catalogue(django_api):
    """It is the first thing a queue-side tool has to be able to say something
    about: a video whose original was never registered gets no formats."""
    django_api.list_video_files_page = pager([])

    catalogue = await read(django_api)

    assert catalogue[VIDEO_ID].files == ()


@pytest.mark.asyncio
async def test_it_pages_through_everything(django_api):
    django_api.list_videos_page = pager([video_row(i) for i in range(1, 1201)])

    assert len(await read(django_api)) == 1200


@pytest.mark.asyncio
async def test_a_short_read_is_refused_rather_than_returned(django_api):
    """A run over half a catalogue reports a total, queues work and exits 0.
    The videos it skipped are then the ones somebody believes are converged."""
    django_api.list_videos_page = pager([video_row(1)], count=900)

    with pytest.raises(IncompleteCatalogue):
        await read(django_api)


@pytest.mark.asyncio
async def test_a_short_read_of_the_unfinished_videos_is_refused_too(django_api):
    """Both passes carry the same weight. A shortfall in the unfinished half
    hides exactly the videos this reads two passes to reach."""
    django_api.list_videos_page = pager([video_row(1, proper_import=False)], count=900)

    with pytest.raises(IncompleteCatalogue, match="unfinished videos"):
        await read(django_api)


@pytest.mark.asyncio
async def test_it_holds_videos_whose_ingest_has_not_finished(django_api):
    """django-api's video list defaults to the public catalogue -- finished
    ingests only. This has to be wider than that: a video mid-ingest, or one
    whose ingest failed, is exactly what a backfill exists to reach."""
    django_api.list_videos_page = pager([video_row(1), video_row(2, proper_import=False)])

    assert set(await read(django_api)) == {"1", "2"}


@pytest.mark.asyncio
async def test_the_videos_own_fields_are_carried(django_api):
    catalogue = await read(django_api)

    assert catalogue[VIDEO_ID].duration == "00:10:00"
    assert catalogue[VIDEO_ID].framerate == 25000


@pytest.mark.asyncio
async def test_nothing_here_claims_to_have_read_the_archive(django_api):
    """The one thing these states must not be mistaken for. A chore handed an
    empty directory listing reports every registered format as missing from the
    archive; handed no listing at all, it says nothing about the archive."""
    catalogue = await read(django_api)

    assert not catalogue[VIDEO_ID].archive_was_read


@pytest.mark.asyncio
async def test_a_row_that_omits_the_revision_reads_as_untracked(django_api):
    """Nothing recorded must read as stale rather than as current."""
    django_api.list_video_files_page = pager(
        [file_row(2, int(VIDEO_ID), "dash", f"{VIDEO_ID}/dash/manifest.mpd", revision=UNSET)]
    )

    catalogue = await read(django_api)

    assert catalogue[VIDEO_ID].files[0].profile_revision == UNTRACKED_REVISION
