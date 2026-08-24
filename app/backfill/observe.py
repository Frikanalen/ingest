"""Finding out what is actually there.

Two sources, fetched differently. django-api is read in bulk -- one paginated
pass over the videos and one over the videofiles gives the whole catalogue, and
asking it per video would be thousands of round trips to learn what two queries
already said. The archive is read per video, because there is no bulk form of
"what is in this directory" and the answer is only wanted for videos something
is going to be done to.

Nothing here decides anything. What to do about what was found is chores' job.
"""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import PurePosixPath

from frikanalen_django_api_client.types import UNSET

from app.archive_store import ArchiveEntry, ArchiveSession
from app.backfill.state import RegisteredFile, VideoState
from app.django_client.service import DjangoApiService, FormatEnum
from app.formats import UNTRACKED_REVISION

logger = getLogger(__name__)

#: How many rows to ask for at a time. Large enough that a catalogue is a
#: handful of requests, small enough not to ask django-api for everything it
#: has in one query.
PAGE_SIZE = 500


class IncompleteSnapshot(RuntimeError):
    """The catalogue could not be read in full.

    Raised rather than returning what did arrive, because the most destructive
    thing here -- deciding a video no longer exists and reclaiming its media --
    reads absence as permission. A partial answer must never be mistaken for a
    complete one.
    """


def _optional(value):
    """Unset and None both mean "django-api did not give us one"."""
    return None if value is UNSET else value


def _profile_revision(row) -> int:
    """Which revision produced this file, however the client spells it.

    Reads the generated attribute when the client has been regenerated against
    a django-api that has the column, and the raw property before then. A row
    that says nothing is UNTRACKED_REVISION, which reads as stale.
    """
    declared = _optional(getattr(row, "profile_revision", None))
    if declared is None:
        declared = row.additional_properties.get("profileRevision")
    return UNTRACKED_REVISION if declared is None else int(declared)


@dataclass(frozen=True)
class CatalogueVideo:
    """What django-api says about a video, aside from its files."""

    duration: str | None = None
    framerate: int | None = None


@dataclass(frozen=True)
class CatalogueSnapshot:
    """Everything django-api knows, read once.

    Held whole rather than queried per video so that a plan describes one
    moment, and so that "this id is not in the catalogue" is a lookup rather
    than a request that might fail for its own reasons.
    """

    videos: Mapping[str, CatalogueVideo] = field(default_factory=dict)
    files: Mapping[str, tuple[RegisteredFile, ...]] = field(default_factory=dict)

    def __contains__(self, video_id: str) -> bool:
        return video_id in self.videos

    def files_for(self, video_id: str) -> tuple[RegisteredFile, ...]:
        return self.files.get(video_id, ())


class Observer:
    """Reads the catalogue and the archive; draws no conclusions from either."""

    def __init__(self, archive: ArchiveSession, django_api: DjangoApiService):
        self.archive = archive
        self.django_api = django_api

    async def snapshot(self) -> CatalogueSnapshot:
        videos, files = await asyncio.gather(self._all_videos(), self._all_files())
        logger.info("Catalogue holds %d videos and %d registered files", len(videos), sum(map(len, files.values())))
        return CatalogueSnapshot(videos=videos, files=files)

    async def _all_videos(self) -> dict[str, CatalogueVideo]:
        videos: dict[str, CatalogueVideo] = {}

        async for row in self._pages(self.django_api.list_videos_page, "videos"):
            videos[str(row.id)] = CatalogueVideo(
                duration=_optional(getattr(row, "duration", None)),
                framerate=_optional(getattr(row, "framerate", None)),
            )
        return videos

    async def _all_files(self) -> dict[str, tuple[RegisteredFile, ...]]:
        files: dict[str, list[RegisteredFile]] = {}

        async for row in self._pages(self.django_api.list_video_files_page, "videofiles"):
            files.setdefault(str(row.video), []).append(
                RegisteredFile(
                    id=row.id,
                    variant=FormatEnum(str(row.variant)),
                    filename=PurePosixPath(row.filename),
                    profile_revision=_profile_revision(row),
                    integrated_lufs=_optional(row.integrated_lufs),
                )
            )
        return {video_id: tuple(rows) for video_id, rows in files.items()}

    async def _pages(self, fetch, what: str):
        """Walk a paginated endpoint, and insist on getting all of it.

        The endpoint states its own total, so a page that comes up short is
        detectable rather than something to be discovered later by a chore
        acting on half a catalogue.
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
            raise IncompleteSnapshot(f"{what} reported {expected} rows but returned {seen}")

    async def archived_video_ids(self) -> list[str]:
        """The video directories in the archive, ignoring its own bookkeeping."""
        entries = await self.archive.list_dir(PurePosixPath("."))
        return sorted(
            (entry.name for entry in entries if entry.is_dir and entry.name.isdigit()),
            key=int,
        )

    async def observe(self, video_id: str, snapshot: CatalogueSnapshot) -> VideoState:
        """Assemble everything the chores need to decide about one video."""
        catalogue = snapshot.videos.get(video_id)

        return VideoState(
            video_id=video_id,
            in_catalogue=catalogue is not None,
            files=snapshot.files_for(video_id),
            directories=await self._archived_directories(video_id),
            duration=catalogue.duration if catalogue else None,
            framerate=catalogue.framerate if catalogue else None,
        )

    async def _archived_directories(self, video_id: str) -> dict[str, tuple[ArchiveEntry, ...]]:
        """Every directory under <id>/, and what is in each.

        The per-directory listings are issued together: over SFTP they pipeline
        down one connection, so a video costs two round trips rather than one
        per format.
        """
        root = PurePosixPath(video_id)
        directories = [entry for entry in await self.archive.list_dir(root) if entry.is_dir]

        contents = await asyncio.gather(*(self.archive.list_dir(entry.path) for entry in directories))
        return dict(zip((entry.name for entry in directories), contents, strict=True))

    async def observe_all(self, video_ids: Iterable[str], snapshot: CatalogueSnapshot):
        for video_id in video_ids:
            yield await self.observe(video_id, snapshot)
