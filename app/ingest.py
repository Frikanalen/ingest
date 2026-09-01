from collections.abc import Awaitable, Callable
from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path
from tempfile import TemporaryDirectory

from frikanalen_django_api_client.models import IngestStateEnum

from app.django_client.service import DjangoApiService, FormatEnum
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.util.file_name_utils import derived_file_location, original_file_location
from app.util.logging import VideoIdFilter

from .archive_store import ArchiveSession, ArchiveStore
from .media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from .media.ffprobe_schema import FfprobeOutput
from .media.loudness.loudness_measurement import LoudnessMeasurement
from .media.loudness.measure import measure_loudness
from .media.segmentation import segmentation_for
from .runner import Task

DESIRED_FORMATS = (
    FormatEnum.LARGE_THUMB,
    FormatEnum.MED_THUMB,
    FormatEnum.SMALL_THUMB,
    FormatEnum.DASH,
)


class Ingester:
    """Archives an upload and the derivatives generated from it.

    Transcoding always reads the uploaded file where tusd left it and writes to
    local scratch space; only finished files are handed to the archive. That
    keeps ffmpeg off the archive entirely, so the archive can live on another
    host.
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

        has_audio = any(stream.codec_type == "audio" for stream in metadata.streams or [])
        loudness = await self._measure_loudness(original_file, has_audio, reporter)

        async with self.archive.open() as archive:
            await self._archive_original(archive, video_id, original_file, metadata, reporter, loudness)

            with TemporaryDirectory(dir=self.work_dir, prefix=f"ingest-{video_id}-") as scratch:
                # No percentage yet: thumbnails are over before ffmpeg would
                # have anything to report, and DASH -- the only other format,
                # and the only one worth timing -- reports its own via
                # _transcode_progress_reporter once it starts.
                await reporter.state(IngestStateEnum.TRANSCODING)
                for file_format in DESIRED_FORMATS:
                    await self._process_format(
                        archive,
                        file_format,
                        metadata,
                        original_file,
                        video_id,
                        Path(scratch),
                        reporter,
                        has_audio,
                        loudness,
                    )

        await self.django_api.set_video_proper_import(video_id, True)
        await reporter.state(IngestStateEnum.DONE, percentage_done=100)

        # Only now is the upload redundant. Leaving it in place on failure keeps
        # the file around for a retry; a re-upload of the same video clears it.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

    async def _measure_loudness(
        self, original_file: Path, has_audio: bool, reporter: IngestReporter
    ) -> LoudnessMeasurement | None:
        """Measure the upload before anything has been done to it.

        This has to happen on the original: it is the figure playout levels
        from to reach its own -23 LUFS target, so it has to describe the
        file as uploaded rather than any derivative we have already
        normalized to something else.

        The pass is analysis only and reports no progress of its own -- it
        decodes audio and discards it, which next to the VP9 ladder is not
        long enough to be worth a percentage.
        """
        if not has_audio:
            return None

        await reporter.state(IngestStateEnum.PROBING)
        loudness = await measure_loudness(original_file)

        if loudness is None:
            self.logger.warning("No usable loudness measurement for %s", original_file)
        else:
            self.logger.info(
                "Measured %s at %.1f LUFS, true peak %s dBTP",
                original_file,
                loudness.integrated_lufs,
                loudness.truepeak_lufs,
            )
        return loudness

    async def _archive_original(
        self,
        archive: ArchiveSession,
        video_id: str,
        original_file: Path,
        metadata: FfprobeOutput,
        reporter: IngestReporter,
        loudness: LoudnessMeasurement | None = None,
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

            await self.django_api.create_video_file(
                filename=str(destination),
                file_format=FormatEnum.ORIGINAL,
                video_id=video_id,
                loudness=loudness,
            )
        except Exception as e:
            self.logger.error("django-api error post original ingest: %s", e)
            await reporter.failed(IngestErrorCode.INTERNAL_ERROR, str(e))
            raise

    async def _process_format(
        self,
        archive: ArchiveSession,
        file_format: FormatEnum,
        metadata: FfprobeOutput,
        source_file: Path,
        video_id: str,
        scratch: Path,
        reporter: IngestReporter,
        has_audio: bool,
        loudness: LoudnessMeasurement | None,
    ):
        self.logger.info("Processing %s as %s", source_file, file_format)

        self.logger.info("Building command for format: %s", file_format)
        template = TemplatedCommandGenerator(file_format)

        # Each format gets a directory of its own, and whatever the command
        # leaves in it is what gets archived. A format can therefore produce a
        # set of files -- DASH writes a manifest and the media it names --
        # without ingest having to know the shape of any of them.
        output_dir = scratch / str(file_format)
        output_dir.mkdir()
        output_file = output_dir / template.metadata.output_name_for(source_file)
        duration_s = float(metadata.format.duration)

        segmentation = segmentation_for(metadata)

        template_args = ProfileTemplateArguments(
            input_file=source_file,
            output_file=output_file,
            output_dir=output_dir,
            scratch_dir=scratch,
            seek_s=(duration_s * 0.25 or 30),
            has_audio=has_audio,
            loudness=loudness,
            frame_rate=segmentation.frame_rate_arg,
            gop_frames=segmentation.gop_frames,
            segment_duration_s=segmentation.segment_duration_arg,
        )

        command = template.render(template_args)
        self.logger.debug("Generated command: %s", command)

        try:
            await Task(
                command,
                duration_s=duration_s,
                passes=template.metadata.passes,
                on_progress=self._transcode_progress_reporter(reporter),
            ).execute()
        except Exception as e:
            self.logger.error("Failed to produce %s: %s", file_format, e)
            await reporter.failed(IngestErrorCode.TRANSCODE_FAILED, str(e))
            raise

        destination = derived_file_location(video_id, str(file_format), output_file)
        self.logger.info("Storing %s to %s", file_format, destination)
        try:
            await self._publish(archive, output_dir, output_file, video_id, file_format)
        except Exception as e:
            self.logger.error("Failed to archive %s: %s", file_format, e)
            await reporter.failed(IngestErrorCode.ARCHIVE_FAILED, str(e))
            raise

        self.logger.info("Creating video file entry for %s", destination)
        await self.django_api.create_video_file(filename=str(destination), file_format=file_format, video_id=video_id)

    async def _publish(
        self,
        archive: ArchiveSession,
        output_dir: Path,
        primary: Path,
        video_id: str,
        file_format: FormatEnum,
    ) -> None:
        """Copy everything one format produced into the archive, primary last.

        The archive is exported read-only to the playout hosts, so a manifest
        arriving before the media it references would be briefly readable and
        broken. Publishing it last means the format either is not there yet or
        is there in full.
        """
        outputs = sorted(output_dir.iterdir(), key=lambda output: output == primary)

        if nested := [output for output in outputs if not output.is_file()]:
            # derived_file_location flattens to a basename, so a template that
            # nested its output would silently archive to the wrong paths.
            raise NotImplementedError(f"{file_format} produced directories, which cannot be archived: {nested}")

        for output in outputs:
            await archive.put(output, derived_file_location(video_id, str(file_format), output))

    def _transcode_progress_reporter(self, reporter: IngestReporter) -> Callable[[float], Awaitable[None]]:
        """Turns ffmpeg's own -progress stream into a percentage.

        Only the DASH template emits -progress; thumbnails are over before
        ffmpeg would have anything to report. So in practice this is the DASH
        encode's own completion fraction, and nothing else moves the bar --
        which matches reality, since a 60s 1080p source measures the DASH
        ladder at roughly 100x the cost of the other three formats combined.

        Reports are throttled to one per whole percentage point: ffmpeg's
        -progress stream updates far more often than that, and each report
        is a call to django-api.
        """
        last_reported = -1

        async def report(fraction: float) -> None:
            nonlocal last_reported
            percentage = round(100 * fraction)
            if percentage != last_reported:
                last_reported = percentage
                await reporter.state(IngestStateEnum.TRANSCODING, percentage_done=percentage)

        return report
