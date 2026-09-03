"""Reading the whole catalogue, once.

django-api is read in bulk -- a paginated pass over the videos and one over the
videofiles gives everything there is -- because asking it per video would be
thousands of round trips to learn what a few queries already said. Held whole
rather than queried per video so that a plan describes one moment, and so that
"this id is not in the catalogue" is a lookup rather than a request that might
fail for its own reasons.

The archive is not read at all, and the `VideoState`s this produces say so.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from logging import getLogger

from app.converge.state import RegisteredFile, VideoState
from app.django_client.service import DjangoApiService

logger = getLogger(__name__)

#: How many rows to ask for at a time. Large enough that a catalogue is a
#: handful of requests, small enough not to ask django-api for everything it
#: has in one query.
PAGE_SIZE = 500


class IncompleteCatalogue(RuntimeError):
    """The catalogue could not be read in full.

    Raised rather than returning what did arrive. A run over half a catalogue
    looks exactly like one over all of it -- it reports a total, it queues
    work, it exits 0 -- and the videos it silently skipped are the ones
    somebody then believes have been converged.
    """


async def read(django_api: DjangoApiService) -> Mapping[str, VideoState]:
    """Every video django-api has, with the files registered against each."""
    videos, files = await asyncio.gather(_videos(django_api), _files(django_api))
    logger.info("Catalogue holds %d videos and %d registered files", len(videos), sum(map(len, files.values())))

    return {video_id: replace(state, files=files.get(video_id, ())) for video_id, state in videos.items()}


async def _videos(django_api: DjangoApiService) -> dict[str, VideoState]:
    """Every video django-api has, finished ingest or not.

    Two passes, because `proper_import` is a boolean filter with no value
    meaning "either" -- and because omitting it does not mean "either", it
    means true. One unfiltered-looking call returns the public catalogue,
    which is every video whose ingest finished, leaving out everything
    mid-ingest and everything whose ingest ever failed. Those are exactly the
    videos this exists to reach.

    The order of the two passes is the interesting part. They are separate
    requests and so separate moments, and a video that changes state in
    between is only seen if it lands in the pass that has not run yet. The
    transition that actually happens is an ingest finishing, false -> true:
    read unfinished first and such a video was already in hand before it
    moved; read finished first and it is in neither page.
    """
    videos: dict[str, VideoState] = {}

    for proper_import, what in ((False, "unfinished videos"), (True, "finished videos")):
        fetch = partial(django_api.list_videos_page, proper_import=proper_import)

        async for row in _pages(fetch, what):
            video_id = str(row.id)
            videos[video_id] = VideoState.from_rows(video_id, row, ())

    return videos


async def _files(django_api: DjangoApiService) -> dict[str, tuple[RegisteredFile, ...]]:
    files: dict[str, list[RegisteredFile]] = {}

    async for row in _pages(django_api.list_video_files_page, "videofiles"):
        files.setdefault(str(row.video), []).append(RegisteredFile.from_row(row))

    return {video_id: tuple(rows) for video_id, rows in files.items()}


async def _pages(fetch, what: str):
    """Walk a paginated endpoint, and insist on getting all of it.

    The endpoint states its own total, so a page that comes up short is
    detectable rather than something to be discovered later by a run acting on
    half a catalogue.
    """
    offset = 0
    expected: int | None = None
    seen = 0

    while True:
        page = await fetch(limit=PAGE_SIZE, offset=offset)
        if expected is None:
            expected = page.count

        rows = page.results or []
        for row in rows:
            seen += 1
            yield row

        if not rows:
            break
        offset += len(rows)
        if seen >= expected:
            break

    if seen != expected:
        raise IncompleteCatalogue(f"{what} reported {expected} rows but returned {seen}")
