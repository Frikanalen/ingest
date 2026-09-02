from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path

from frikanalen_django_api_client.models import IngestKindEnum, IngestStateEnum, VideoFileVariantEnum

from app.backfill.enqueue import UPLOAD_PRIORITY
from app.django_client.service import DjangoApiService
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.util.file_name_utils import original_file_location
from app.util.logging import VideoIdFilter

from .archive_store import ArchiveSession, ArchiveStore
from .media.ffprobe_schema import FfprobeOutput


class Ingester:
    """Gets an upload into the archive, then hands the rest to the queue.

    This does exactly what needs the file where tusd left it, and no more:
    record that the upload happened, put the original in the archive, register
    it, and queue a job. Everything after that -- loudness, duration, framerate
    and every derived format -- is a worker's, because once the original is
    archived there is nothing about an upload that distinguishes it from any
    other video, and the observe-plan-apply that decides what a video needs
    already exists.

    That split is not arbitrary. The upload volume is ReadWriteOnce, which is
    what pins this pod to a single replica; a worker mounts no upload volume,
    which is why the pool scales. Archiving is the step that turns a file only
    one pod can see into a file every worker can, so it is the last thing that
    has to happen here.

    What it costs: the video is `pending` between this returning and a worker
    claiming it, and if no worker ever does, nothing here will notice. What it
    buys: an upload that survives this pod restarting, a bound on how many
    ladders run at once, and one implementation of what a video should have
    rather than two.
    """

    django_api: DjangoApiService
    archive: ArchiveStore
    logger: Logger

    def __init__(self, archive: ArchiveStore, django_api: DjangoApiService):
        # No work_dir: nothing here transcodes any more, so there is no scratch
        # space to point anywhere. The workers have one.
        self.logger = getLogger(__name__)
        self.archive = archive
        self.django_api = django_api

    async def ingest(
        self, video_id: str, original_file: Path, metadata: FfprobeOutput, reporter: IngestReporter | None = None
    ):
        self.logger.addFilter(VideoIdFilter(video_id))
        self.logger.info("Ingesting file with video_id: %s, original_file: %s", video_id, original_file)

        # A caller that already reported the probe passes its reporter in, so
        # the whole run reads as one sequence of states rather than restarting
        # partway through.
        reporter = reporter or IngestReporter(self.django_api, video_id)

        try:
            await self.django_api.set_video_uploaded_time(video_id, datetime.now())
        except Exception as e:
            self.logger.error("Failed to set video uploaded time: %s", e)
            await reporter.failed(IngestErrorCode.INTERNAL_ERROR, str(e))
            raise

        async with self.archive.open() as archive:
            await self._archive_original(archive, video_id, original_file, metadata, reporter)

        await self._enqueue(video_id, reporter)

        # Only now is the upload redundant, and only because the archive holds
        # the same bytes: the job that was just queued reads the original from
        # there, and so does every retry after it. Before this commit the file
        # was kept until every format had been built, because the upload was
        # the only source; it no longer is.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

    async def _enqueue(self, video_id: str, reporter: IngestReporter) -> None:
        """Queue the work that follows archiving.

        Before the unlink, deliberately. If this fails, the original is in the
        archive but nothing is coming for it, and the upload still sitting in
        the tusd volume is the evidence -- whereas unlinking first would leave
        a video that is archived, unqueued, and indistinguishable from one that
        finished. Either way `fk-backfill apply <id>` is the recovery.

        The kind is sent explicitly because the column it would otherwise
        default to decides which pool may claim the job, and because the
        completion step keys on it: only an upload promises that the video ends
        importable.
        """
        try:
            await self.django_api.enqueue_ingest_job(
                video_id,
                kind=IngestKindEnum.UPLOAD,
                priority=UPLOAD_PRIORITY,
            )
        except Exception as e:
            self.logger.error("Failed to queue video %s: %s", video_id, e)
            await reporter.failed(IngestErrorCode.INTERNAL_ERROR, str(e))
            raise

        self.logger.info("Queued video %s at priority %d", video_id, UPLOAD_PRIORITY)

    async def _archive_original(
        self,
        archive: ArchiveSession,
        video_id: str,
        original_file: Path,
        metadata: FfprobeOutput,
        reporter: IngestReporter,
    ):
        destination = original_file_location(video_id, Path(original_file.name))
        await reporter.state(IngestStateEnum.ARCHIVING)

        try:
            self.logger.info("Storing original file at %s", destination)
            await archive.assert_absent(destination)
            await archive.put(original_file, destination)
        except Exception as e:
            self.logger.error("Failed to store original file in archive: %s", e)
            await reporter.failed(IngestErrorCode.ARCHIVE_FAILED, str(e))
            raise

        try:
            self.logger.info("Setting video duration: %s", metadata.format.duration)
            await self.django_api.set_video_duration(video_id, metadata.format.duration)

            # No loudness. It is measured from the original, and the worker
            # fetches the original anyway for the formats -- measuring here as
            # well would be a second full pass over the audio to learn the same
            # number. The metadata chore asks for it and fills it in.
            await self.django_api.create_video_file(
                filename=str(destination),
                file_format=VideoFileVariantEnum.ORIGINAL,
                video_id=video_id,
            )
        except Exception as e:
            self.logger.error("django-api error post original ingest: %s", e)
            await reporter.failed(IngestErrorCode.INTERNAL_ERROR, str(e))
            raise
