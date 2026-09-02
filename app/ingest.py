from dataclasses import replace
from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path

from frikanalen_django_api_client.models import IngestStateEnum, VideoFileVariantEnum

from app.backfill.apply import Applier, SourceUnavailable
from app.backfill.chores import CONVERGENCE_CHORES, DesiredState, plan
from app.backfill.observe import Observer
from app.django_client.service import DjangoApiService
from app.ingest_reporting import IngestErrorCode, IngestReporter, transcode_progress_reporter
from app.media.produce import PublishFailed, SourceMedia, TranscodeFailed
from app.util.file_name_utils import original_file_location
from app.util.logging import VideoIdFilter

from .archive_store import ArchiveError, ArchiveSession, ArchiveStore
from .media.ffprobe_schema import FfprobeOutput
from .media.loudness.loudness_measurement import LoudnessMeasurement
from .media.loudness.measure import measure_loudness


class Ingester:
    """Archives an upload, then converges the video on the desired state.

    Transcoding always reads the uploaded file where tusd left it and writes to
    local scratch space; only finished files are handed to the archive. That
    keeps ffmpeg off the archive entirely, so the archive can live on another
    host.

    What gets built is not this class's decision. Once the original is in the
    archive, an upload is a video like any other, and the same observe-plan-
    apply a worker runs decides what it is missing -- so the two paths cannot
    disagree about what a video is supposed to have, which is the drift this
    whole arrangement exists to prevent. It also gets the upload path metadata
    it had no way to record: framerate is derived from the source and, until
    the plan started asking for it, was written by nothing.

    Note what this does not yet fix. A plan will skip a format that is already
    there at the current revision, but a second attempt at the same upload does
    not reach the planner: `_archive_original` asserts the original is absent
    and raises first. Re-entering this video is `fk-backfill apply <id>`, and
    making the hook itself idempotent is a separate question about what a
    re-upload should mean.
    """

    django_api: DjangoApiService
    archive: ArchiveStore
    work_dir: Path | None
    logger: Logger

    def __init__(
        self,
        archive: ArchiveStore,
        django_api: DjangoApiService,
        work_dir: Path | None = None,
    ):
        self.logger = getLogger(__name__)
        self.archive = archive
        self.django_api = django_api
        self.work_dir = work_dir

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

        source = SourceMedia.probed(video_id, original_file, metadata)
        source = replace(source, loudness=await self._measure_loudness(source, reporter))

        async with self.archive.open() as archive:
            await self._archive_original(archive, source, reporter)
            await self._converge(archive, source, reporter)

        await self.django_api.set_video_proper_import(video_id, True)
        await reporter.state(IngestStateEnum.DONE, percentage_done=100)

        # Only now is the upload redundant. Leaving it in place on failure keeps
        # the file around for a retry; a re-upload of the same video clears it.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

    async def _converge(
        self,
        archive: ArchiveSession,
        source: SourceMedia,
        reporter: IngestReporter,
    ) -> None:
        """Build whatever the video turns out to be missing.

        Deliberately a plan rather than a loop over DESIRED_FORMATS. The
        difference is not what a fresh upload gets -- for a video with nothing
        archived the plan is every format, which is what the loop did -- but
        that the answer now comes from the same place the worker's does. A
        format added to DESIRED_FORMATS, or a template whose revision moves,
        reaches both paths or neither.

        The plan is carried out against the upload rather than the archived
        copy. They are the same bytes, and fetching them back would be
        gigabytes over SFTP plus a second loudness pass to learn what probing
        already told us.
        """
        observed = await Observer(archive, self.django_api).observe_one(source.video_id)
        work = plan(observed, DesiredState.from_templates(), chores=CONVERGENCE_CHORES)

        # Reported, never acted on -- media nothing claims, a row whose file is
        # missing. Worth a line in the log even when there is nothing to do.
        for note in work.notes:
            self.logger.warning("video %s: %s", source.video_id, note)

        if not work:
            self.logger.info("Nothing to do for video %s", source.video_id)
            return

        self.logger.info("Plan for video %s:\n%s", source.video_id, work.describe())

        # No percentage yet: thumbnails are over before ffmpeg would have
        # anything to report, and DASH -- the only format worth timing --
        # reports its own via transcode_progress_reporter once it starts.
        await reporter.state(IngestStateEnum.TRANSCODING)

        try:
            await Applier(archive, self.django_api, self.work_dir).apply(
                work,
                on_progress=transcode_progress_reporter(reporter),
                source=source,
            )
        except TranscodeFailed as e:
            await reporter.failed(IngestErrorCode.TRANSCODE_FAILED, str(e))
            raise
        except (PublishFailed, SourceUnavailable, ArchiveError) as e:
            await reporter.failed(IngestErrorCode.ARCHIVE_FAILED, str(e))
            raise

    async def _measure_loudness(self, source: SourceMedia, reporter: IngestReporter) -> LoudnessMeasurement | None:
        """Measure the upload before anything has been done to it.

        This has to happen on the original: it is the figure playout levels
        from to reach its own -23 LUFS target, so it has to describe the
        file as uploaded rather than any derivative we have already
        normalized to something else.

        The pass is analysis only and reports no progress of its own -- it
        decodes audio and discards it, which next to the VP9 ladder is not
        long enough to be worth a percentage.
        """
        if not source.has_audio:
            return None

        await reporter.state(IngestStateEnum.PROBING)
        loudness = await measure_loudness(source.path)

        if loudness is None:
            self.logger.warning("No usable loudness measurement for %s", source.path)
        else:
            self.logger.info(
                "Measured %s at %.1f LUFS, true peak %s dBTP",
                source.path,
                loudness.integrated_lufs,
                loudness.truepeak_lufs,
            )
        return loudness

    async def _archive_original(
        self,
        archive: ArchiveSession,
        source: SourceMedia,
        reporter: IngestReporter,
    ):
        destination = original_file_location(source.video_id, Path(source.path.name))
        await reporter.state(IngestStateEnum.ARCHIVING)

        try:
            self.logger.info("Storing original file at %s", destination)
            await archive.assert_absent(destination)
            await archive.put(source.path, destination)
        except Exception as e:
            self.logger.error("Failed to store original file in archive: %s", e)
            await reporter.failed(IngestErrorCode.ARCHIVE_FAILED, str(e))
            raise

        try:
            self.logger.info("Setting video duration: %s", source.metadata.format.duration)
            await self.django_api.set_video_duration(source.video_id, source.metadata.format.duration)

            await self.django_api.create_video_file(
                filename=str(destination),
                file_format=VideoFileVariantEnum.ORIGINAL,
                video_id=source.video_id,
                loudness=source.loudness,
            )
        except Exception as e:
            self.logger.error("django-api error post original ingest: %s", e)
            await reporter.failed(IngestErrorCode.INTERNAL_ERROR, str(e))
            raise
