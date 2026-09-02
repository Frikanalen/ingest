"""Putting videos in the queue.

Enqueueing is a PUT that replaces a video's ingest job, and the job is keyed on
the video itself -- one row, no history. So the only thing that can go wrong is
overwriting a job that is currently being worked, which would reset a member's
upload to `pending` under them and invite a second worker onto a video whose
original is still being archived.

There is no conditional PUT to prevent that, so the state is read first and an
active job is left alone. That is a read then a write with a gap in between,
which is a small race rather than no race -- but the window is milliseconds
against jobs that last minutes, and the alternative is not checking at all.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from logging import getLogger

from frikanalen_django_api_client.models import IngestKindEnum, IngestStateEnum

from app.django_client.service import DjangoApiService

logger = getLogger(__name__)

#: A job in one of these is being worked right now. Overwriting it would take
#: the video away from whoever holds it, and tell the person watching their
#: upload that it had gone back to the beginning.
ACTIVE_STATES = frozenset(
    {
        IngestStateEnum.PROBING,
        IngestStateEnum.ARCHIVING,
        IngestStateEnum.TRANSCODING,
    }
)

#: Where a member's upload goes in relative to a backfill, which goes in at 0.
#: Claiming does not preempt -- it hands out the highest-priority job that is
#: waiting, not one already in progress -- so this decides what a free worker
#: picks up next rather than interrupting an encode.
UPLOAD_PRIORITY = 100


def is_someone_elses(job) -> bool:
    """Whether this job belongs to work already under way.

    Two cases. A job in an active state is held by a worker right now. And a
    queued upload has not been claimed yet but is still somebody's: PUTting
    over it would drop it to backfill priority behind everything else and take
    away the kind the completion step keys on, so the video would eventually
    finish without ever being marked importable -- and the member would watch
    their upload go back to the end of the queue.
    """
    if job.state in ACTIVE_STATES:
        return True

    return getattr(job, "kind", None) == IngestKindEnum.UPLOAD and job.state == IngestStateEnum.PENDING

#: Reads and writes are small and independent, so they overlap; the cap is
#: politeness toward django-api rather than a limit of ours.
DEFAULT_CONCURRENCY = 8


@dataclass
class EnqueueReport:
    enqueued: list[str] = field(default_factory=list)
    already_running: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        lines = [f"{len(self.enqueued)} videos queued."]
        if self.already_running:
            lines.append(
                f"{len(self.already_running)} left alone: ingest is working on them now "
                f"({', '.join(self.already_running[:5])}{'...' if len(self.already_running) > 5 else ''})."
            )
        if self.failed:
            lines.append(f"{len(self.failed)} could not be queued:")
            lines += [f"  {video_id}: {why}" for video_id, why in list(self.failed.items())[:10]]
        return "\n".join(lines)


class Enqueuer:
    """Marks videos pending, without disturbing work already under way."""

    def __init__(
        self,
        django_api: DjangoApiService,
        *,
        kind: str = "backfill",
        priority: int = 0,
        concurrency: int = DEFAULT_CONCURRENCY,
    ):
        self.django_api = django_api
        self.kind = kind
        self.priority = priority
        self._slots = asyncio.Semaphore(concurrency)

    async def enqueue_all(self, video_ids: Iterable[str]) -> EnqueueReport:
        # Materialized because it is walked twice, and the caller's is usually
        # a generator over a plan: the second pass would come up empty and
        # every result would be silently dropped.
        wanted = list(video_ids)

        report = EnqueueReport()
        results = await asyncio.gather(
            *(self._enqueue(video_id) for video_id in wanted),
            return_exceptions=True,
        )

        for video_id, result in zip(wanted, results, strict=True):
            if isinstance(result, BaseException):
                report.failed[video_id] = str(result)
            elif result:
                report.enqueued.append(video_id)
            else:
                report.already_running.append(video_id)
        return report

    async def _enqueue(self, video_id: str) -> bool:
        """True if it went in the queue, False if it was already being worked."""
        async with self._slots:
            job = await self.django_api.get_ingest_job(video_id)

            if job is not None and is_someone_elses(job):
                logger.info("Leaving video %s alone: ingest reports %s", video_id, job.state)
                return False

            await self.django_api.enqueue_ingest_job(video_id, kind=self.kind, priority=self.priority)
            return True
