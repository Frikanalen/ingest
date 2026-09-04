"""Draining the ingest queue.

A worker asks for a job only when it has capacity for one, so nothing has to
decide where work goes: adding a worker is starting another process, and there
is no dispatcher, no load balancer and no back-pressure protocol between them.

The same loop serves every kind of work. A job names a video, and what that
video needs is worked out when it is claimed rather than when it was queued,
so nothing here has to know why the job exists -- only that its source is in
the archive, which after the hook has done its part is true of all of them.
"""

import asyncio
import contextlib
import signal
from logging import getLogger
from pathlib import Path

from frikanalen_django_api_client.models import IngestKindEnum, IngestStateEnum

from app.archive_store import ArchiveError, ArchiveStore
from app.converge.apply import Applier, SourceUnavailable
from app.converge.chores import DesiredState, plan
from app.converge.observe import Observer
from app.django_client.service import DjangoApiService
from app.ingest_reporting import IngestErrorCode, IngestReporter, transcode_progress_reporter
from app.media.produce import PublishFailed, TranscodeFailed
from app.util.logging import VideoIdFilter

logger = getLogger(__name__)


class Worker:
    """Claims one video at a time and converges it toward the desired state."""

    def __init__(
        self,
        archive: ArchiveStore,
        django_api: DjangoApiService,
        *,
        name: str,
        kind: str | None = None,
        work_dir: Path | None = None,
        poll_interval_s: float = 30.0,
    ):
        self.archive = archive
        self.django_api = django_api
        self.name = name
        self.kind = kind
        self.work_dir = work_dir
        self.poll_interval_s = poll_interval_s
        self._draining = asyncio.Event()

    @property
    def draining(self) -> bool:
        return self._draining.is_set()

    def drain(self) -> None:
        """Stop taking new work. Whatever is in hand is still finished.

        Abandoning a claim mid-encode is survivable -- the lease expires and
        someone picks it up -- but it throws away however many hours have gone
        into it, so a worker being retired asks for nothing further and sees
        out the job it has.
        """
        if not self.draining:
            logger.info("Draining: no further jobs will be claimed")
        self._draining.set()

    async def run(self) -> None:
        while not self.draining:
            try:
                claimed = await self.run_once()
            except Exception:
                # A failure to claim, or to report, is not this job's fault and
                # must not take the worker down with it: the pod would restart
                # into the same broken API and the queue would stop draining.
                logger.exception("Claim cycle failed; retrying after the poll interval")
                claimed = False

            if not claimed:
                await self._wait_before_asking_again()

        logger.info("Drained")

    async def _wait_before_asking_again(self) -> None:
        """Sleep, but wake immediately if we are told to stop."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._draining.wait(), timeout=self.poll_interval_s)

    async def run_once(self) -> bool:
        """Take one job if there is one. False means the queue was empty."""
        job = await self.django_api.claim_ingest_job(worker=self.name, kind=self.kind)
        if job is None:
            return False

        video_id = str(job.video)
        logger.addFilter(VideoIdFilter(video_id))
        logger.info("Claimed video %s", video_id)

        if self.draining:
            # SIGTERM landed while this claim was in flight. Starting the job
            # now would mean starting hours of work with only the grace period
            # left to do it in, and being killed partway is the one outcome
            # that actually loses the encoding -- so let it go. The lease
            # expires and a worker that has time for it claims it again, which
            # costs a lease timeout rather than however long we would have got
            # through before SIGKILL.
            logger.info("Drained mid-claim; releasing video %s to its lease", video_id)
            return True

        await self._work(video_id, job.kind)
        return True

    async def _work(self, video_id: str, kind=None) -> None:
        reporter = IngestReporter(self.django_api, video_id)

        try:
            async with self.archive.open() as archive:
                state = await Observer(archive, self.django_api).observe_one(video_id)
                work = plan(state, DesiredState.from_templates())

                if not work:
                    logger.info("Nothing to do for video %s", video_id)
                else:
                    logger.info("Plan for video %s:\n%s", video_id, work.describe())
                    await reporter.state(IngestStateEnum.TRANSCODING)
                    await Applier(archive, self.django_api, self.work_dir).apply(
                        work, on_progress=transcode_progress_reporter(reporter)
                    )

                for note in work.notes:
                    logger.warning("video %s: %s", video_id, note)

            if kind == IngestKindEnum.UPLOAD:
                # The upload contract, and only the upload's: this video is now
                # everything it was supposed to be, so the catalogue may show
                # it. A backfill makes no such promise -- flipping the flag on
                # a legacy video would publish something the catalogue is
                # currently, and perhaps deliberately, hiding.
                #
                # Inside the try, so a failure here is a failed job rather than
                # an exception escaping past the DONE report.
                await self.django_api.set_video_proper_import(video_id, True)

        except TranscodeFailed as e:
            await self._fail(reporter, IngestErrorCode.TRANSCODE_FAILED, e)
        except (PublishFailed, SourceUnavailable, ArchiveError) as e:
            await self._fail(reporter, IngestErrorCode.ARCHIVE_FAILED, e)
        except Exception as e:
            await self._fail(reporter, IngestErrorCode.INTERNAL_ERROR, e)
        else:
            await reporter.state(IngestStateEnum.DONE, percentage_done=100)
            logger.info("Finished video %s", video_id)

    async def _fail(self, reporter: IngestReporter, code: IngestErrorCode, error: Exception) -> None:
        """Record the failure and carry on to the next job.

        Deliberately not re-raised. One video that cannot be processed is not a
        reason to stop processing the rest, and the state we just wrote is what
        stops it being claimed again immediately.
        """
        logger.error("video %s failed: %s", reporter.video_id, error, exc_info=True)
        await reporter.failed(code, str(error))


def install_drain_handlers(worker: Worker) -> None:
    """Turn a shutdown signal into "stop claiming" rather than "stop".

    Kubernetes sends SIGTERM and then waits terminationGracePeriodSeconds --
    thirty by default, which is nothing against a VP9 ladder. Set the grace
    period to something a job can actually finish in, or accept that scaling
    down mid-encode costs the work in progress.
    """
    loop = asyncio.get_running_loop()
    for received in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(received, worker.drain)


async def _drain_the_queue() -> int:
    from frikanalen_django_api_client import AuthenticatedClient

    from app.archive_store import create_archive_store
    from app.util.lifespan import get_token
    from app.util.settings import get_settings

    settings = get_settings()

    # Built before the first claim so a broken archive config fails the pod
    # rather than the first video unlucky enough to be claimed by it.
    archive = create_archive_store(settings.archive)
    logger.info("Archiving to %s", archive)

    async with AuthenticatedClient(
        base_url=str(settings.api.url),
        token=get_token(settings.api),
        prefix="Token",
        raise_on_unexpected_status=True,
        follow_redirects=True,
    ) as client:
        worker = Worker(
            archive,
            DjangoApiService(client),
            name=settings.worker.identify(),
            kind=settings.worker.kind,
            work_dir=settings.work_dir,
            poll_interval_s=settings.worker.poll_interval_s,
        )
        install_drain_handlers(worker)

        logger.info("Worker %s draining %s jobs", worker.name, worker.kind or "any")
        await worker.run()

    return 0


def main() -> int:
    import logging

    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_drain_the_queue())


if __name__ == "__main__":
    raise SystemExit(main())
