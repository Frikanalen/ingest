"""A django-api stand-in that remembers what it was told.

An AsyncMock answers every read with another mock, which is fine for code that
only writes. Ingest no longer only writes: once the original is archived it
observes the video and plans the difference, exactly as a worker does, so a
mock that could not tell it the original had been registered would leave it
planning nothing and building nothing -- the tests would pass an ingest that
did almost none of its job.

This keeps just enough state for that read-back to be truthful: the videofile
rows that have been created, and the two video fields ingest writes. Everything
else is still an AsyncMock, so call assertions work as they always did.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from frikanalen_django_api_client.models import IngestStateEnum

from app.formats import UNTRACKED_REVISION


def registered_file(row_id: int, video_id: str, variant, filename: str, revision: int = 1) -> SimpleNamespace:
    """A videofile row as django-api would serve it back."""
    return SimpleNamespace(
        id=row_id,
        video=int(video_id),
        variant=variant,
        filename=filename,
        integrated_lufs=None,
        profile_revision=revision,
        additional_properties={},
    )


def recording_django_api(video_id: str, files: list[SimpleNamespace] | None = None) -> AsyncMock:
    """An AsyncMock whose reads reflect the writes made through it.

    `files` seeds rows that were registered before this ingest started, for the
    cases that turn on ingest finding something already there.
    """
    api = AsyncMock()

    video = SimpleNamespace(id=int(video_id), duration=None, framerate=None)
    rows: list[SimpleNamespace] = list(files or [])

    async def create_video_file(*, filename, video_id, file_format, loudness=None, profile_revision=None):
        rows.append(
            SimpleNamespace(
                id=max((row.id for row in rows), default=0) + 1,
                video=int(video_id),
                variant=file_format,
                filename=filename,
                integrated_lufs=loudness.integrated_lufs if loudness else None,
                # The real API column is NOT NULL with a zero default, and zero
                # is what "produced before we recorded this" reads as.
                profile_revision=UNTRACKED_REVISION if profile_revision is None else profile_revision,
                additional_properties={},
            )
        )

    async def set_video_duration(_video_id, duration):
        video.duration = duration

    async def set_video_framerate(_video_id, framerate):
        video.framerate = framerate

    async def get_video(_video_id):
        return video

    async def get_files_for_video(_video_id):
        return SimpleNamespace(count=len(rows), results=list(rows))

    # The queue, such as it is: one job per video, which is what django-api
    # holds too. Enough for a test to hand a worker the job the hook just
    # queued and watch the two halves meet.
    queue: dict[str, SimpleNamespace] = {}

    async def enqueue_ingest_job(a_video_id, kind, priority):
        queue[str(a_video_id)] = SimpleNamespace(
            video=int(a_video_id),
            kind=kind,
            priority=priority,
            state=IngestStateEnum.PENDING,
        )

    async def get_ingest_job(a_video_id):
        return queue.get(str(a_video_id))

    async def claim_ingest_job(worker, kind=None):
        """Hand out the highest-priority waiting job, as the real endpoint does."""
        waiting = [
            job
            for job in queue.values()
            if job.state == IngestStateEnum.PENDING and (kind is None or job.kind == kind)
        ]
        if not waiting:
            return None

        claimed = max(waiting, key=lambda job: job.priority)
        claimed.state = IngestStateEnum.PROBING
        return claimed

    api.create_video_file.side_effect = create_video_file
    api.set_video_duration.side_effect = set_video_duration
    api.set_video_framerate.side_effect = set_video_framerate
    api.get_video.side_effect = get_video
    api.get_files_for_video.side_effect = get_files_for_video
    api.enqueue_ingest_job.side_effect = enqueue_ingest_job
    api.get_ingest_job.side_effect = get_ingest_job
    api.claim_ingest_job.side_effect = claim_ingest_job

    return api
