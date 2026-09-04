from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path, PurePosixPath

from frikanalen_django_api_client.models import IngestKindEnum, IngestStateEnum, VideoFileVariantEnum

from app.django_client.service import DjangoApiService
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.util.file_name_utils import IMAGES_DIR, original_file_location
from app.util.logging import VideoIdFilter

from .archive_store import ArchiveSession, ArchiveStore
from .media.ffprobe_schema import FfprobeOutput

#: Where a member's upload goes in relative to work an operator queued, which
#: goes in at 0. Claiming does not preempt -- it hands out the highest-priority
#: job that is waiting, not one already in progress -- so this decides what a
#: free worker picks up next rather than interrupting an encode.
UPLOAD_PRIORITY = 100


class Ingester:
    """Gets an upload into the archive, then hands the rest to the queue.

    This does exactly what needs the file where tusd left it, and no more:
    record that the upload happened, supersede whatever the last upload to this
    video left behind, put the original in the archive, register it, and queue
    a job. Everything after that -- loudness, duration, framerate and every
    derived format -- is a worker's, because once the original is archived
    there is nothing about an upload that distinguishes it from any other
    video, and the observe-plan-apply that decides what a video needs already
    exists.

    The supersede is the one step that has no counterpart in a backfill, and it
    is why: an upload is the only event that says the *source* has changed. A
    backfill must never touch the original, and reaches this video knowing only
    that its derivatives may be stale. See _supersede_previous_media.

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
            # Reported before the supersede rather than inside the put, because
            # taking the previous media out of the published tree is already
            # the archive being rearranged on this video's behalf, and a member
            # watching should see that as archiving rather than as a probe that
            # stopped responding.
            await reporter.state(IngestStateEnum.ARCHIVING)
            await self._supersede_previous_media(archive, video_id, reporter)
            await self._archive_original(archive, video_id, original_file, metadata, reporter)

        await self._enqueue(video_id, reporter)

        # Only now is the upload redundant, and only because the archive holds
        # the same bytes: the job that was just queued reads the original from
        # there, and so does every retry after it. Before this commit the file
        # was kept until every format had been built, because the upload was
        # the only source; it no longer is.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

    async def _supersede_previous_media(self, archive: ArchiveSession, video_id: str, reporter: IngestReporter) -> None:
        """Take out whatever a previous upload to this id left behind.

        A second upload to a video replaces the first. That is the only reading
        that serves the member -- they sent a new file because they want the
        new file broadcast, and there is no other way to correct a video
        without abandoning its id, its schedule slots and every link to it --
        and it is the only one that leaves the archive in a state the rest of
        the system can work with. `original/` has to hold exactly one file or
        the video is unprocessable by every job that will ever look at it, and
        a second upload under a different name is precisely what used to put
        two there.

        Nothing is destroyed. `trash()` is a rename into `.trash/`, purged
        separately and deliberately, so an upload sent to the wrong video costs
        someone a rename back rather than a restore from backup. This is not
        gated on the video being unfinished: the mistake a member most often
        needs to undo -- the wrong cut, the wrong language track -- is one they
        only discover *after* the import completed, and refusing them there
        would leave the case this exists for unserved.

        Programme images are left where they are. They live under
        `<id>/images/`, are registered in a different table, and describe the
        programme rather than its media; taking them along would leave rows
        naming files that are no longer there, which is an incident rather
        than a tidy-up.

        The order is trash, then unregister, matching the plans the backfill
        applies. Reversed, a failure between the two would have destroyed the
        record of media it then failed to remove. This way a run interrupted
        anywhere in here is finished by the retry: the step is entered when
        *either* the archive or the catalogue still has something, so whatever
        is still published is still trashed and whatever is still registered is
        still dropped.
        """
        registered = await self.django_api.get_files_for_video(video_id)
        rows = list(registered.results or [])
        superseded = [
            entry
            for entry in await archive.list_dir(PurePosixPath(video_id))
            if entry.is_dir and entry.name != IMAGES_DIR
        ]

        if not rows and not superseded:
            return

        self.logger.warning(
            "Video %s already has media; this upload supersedes it (%d directories, %d registered files)",
            video_id,
            len(superseded),
            len(rows),
        )

        try:
            for entry in superseded:
                trashed = await archive.trash(entry.path)
                self.logger.info("Superseded by a new upload: %s is now at %s", entry.path, trashed)

            for row in rows:
                self.logger.info("Unregistering videofile %s (%s): its file has been trashed", row.id, row.filename)
                await self.django_api.delete_video_file(row.id)
        except Exception as e:
            self.logger.error("Failed to supersede the previous media for video %s: %s", video_id, e)
            await reporter.failed(IngestErrorCode.ARCHIVE_FAILED, str(e))
            raise

    async def _enqueue(self, video_id: str, reporter: IngestReporter) -> None:
        """Queue the work that follows archiving.

        Before the unlink, deliberately. If this fails, the original is in the
        archive but nothing is coming for it, and the upload still sitting in
        the tusd volume is the evidence -- whereas unlinking first would leave
        a video that is archived, unqueued, and indistinguishable from one that
        finished. Either way `fk archive backfill <id> --apply` is the recovery.

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

        try:
            self.logger.info("Storing original file at %s", destination)
            # Asked first, though put() would refuse an occupied destination
            # anyway: the refusal it saves is the one that would arrive after
            # twenty gigabytes had already crossed the wire. What the check is
            # for is a file that appeared between the supersede above and here.
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
