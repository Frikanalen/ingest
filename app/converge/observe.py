"""Finding out what is actually there, for one video.

Two sources. django-api is asked about the video it was handed, and the archive
is listed under that video's directory; neither is read in bulk, because a
worker has been given a single video and asking for every row in the database
to learn about one of them would be absurd.

Nothing here decides anything. What to do about what was found is chores' job.
"""

import asyncio
from logging import getLogger
from pathlib import PurePosixPath

from app.archive_store import ArchiveEntry, ArchiveSession
from app.converge.state import RegisteredFile, VideoState
from app.django_client.service import DjangoApiService

logger = getLogger(__name__)


class Observer:
    """Reads the catalogue and the archive; draws no conclusions from either."""

    def __init__(self, archive: ArchiveSession, django_api: DjangoApiService):
        self.archive = archive
        self.django_api = django_api

    async def observe_one(self, video_id: str) -> VideoState:
        """Assemble everything the chores need to decide about one video."""
        video, files, directories = await asyncio.gather(
            self.django_api.get_video(video_id),
            self.django_api.get_files_for_video(video_id),
            self._archived_directories(video_id),
        )

        return VideoState.from_rows(
            video_id,
            video,
            tuple(RegisteredFile.from_row(row) for row in (files.results or [])),
            directories=directories,
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
