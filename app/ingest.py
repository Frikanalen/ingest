from dataclasses import replace
from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path
from tempfile import TemporaryDirectory

from frikanalen_django_api_client.models import IngestStateEnum

from app.django_client.service import DjangoApiService, FormatEnum
from app.formats import DESIRED_FORMATS
from app.ingest_reporting import IngestErrorCode, IngestReporter, transcode_progress_reporter
from app.media.produce import FormatProducer, PublishFailed, SourceMedia, TranscodeFailed
from app.util.file_name_utils import original_file_location
from app.util.logging import VideoIdFilter

from .archive_store import ArchiveSession, ArchiveStore
from .media.ffprobe_schema import FfprobeOutput
from .media.loudness.loudness_measurement import LoudnessMeasurement
from .media.loudness.measure import measure_loudness


class Ingester:
    """Archives an upload and the derivatives generated from it.

    Transcoding always reads the uploaded file where tusd left it and writes to
    local scratch space; only finished files are handed to the archive. That
    keeps ffmpeg off the archive entirely, so the archive can live on another
    host.

    Producing each format is FormatProducer's job, not this class's: a backfill
    runs exactly the same code against a source it fetched from the archive
    instead of one tusd left behind.
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

            producer = FormatProducer(archive, self.django_api)

            with TemporaryDirectory(dir=self.work_dir, prefix=f"ingest-{video_id}-") as scratch:
                # No percentage yet: thumbnails are over before ffmpeg would
                # have anything to report, and DASH -- the only other format,
                # and the only one worth timing -- reports its own via
                # _transcode_progress_reporter once it starts.
                await reporter.state(IngestStateEnum.TRANSCODING)
                for file_format in DESIRED_FORMATS:
                    await self._produce(producer, source, file_format, Path(scratch), reporter)

        await self.django_api.set_video_proper_import(video_id, True)
        await reporter.state(IngestStateEnum.DONE, percentage_done=100)

        # Only now is the upload redundant. Leaving it in place on failure keeps
        # the file around for a retry; a re-upload of the same video clears it.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

    async def _produce(
        self,
        producer: FormatProducer,
        source: SourceMedia,
        file_format: FormatEnum,
        scratch: Path,
        reporter: IngestReporter,
    ) -> None:
        """Run one format, and say what went wrong in the uploader's terms.

        The producer reports failures as what they were rather than as an error
        code, because a backfill has no uploader to answer to and would
        classify the same failure differently.
        """
        try:
            await producer.produce(
                source,
                file_format,
                scratch,
                on_progress=transcode_progress_reporter(reporter),
            )
        except TranscodeFailed as e:
            await reporter.failed(IngestErrorCode.TRANSCODE_FAILED, str(e))
            raise
        except PublishFailed as e:
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
                file_format=FormatEnum.ORIGINAL,
                video_id=source.video_id,
                loudness=source.loudness,
            )
        except Exception as e:
            self.logger.error("django-api error post original ingest: %s", e)
            await reporter.failed(IngestErrorCode.INTERNAL_ERROR, str(e))
            raise
