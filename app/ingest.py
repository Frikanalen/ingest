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
from .runner import Task

DESIRED_FORMATS = (
    FormatEnum.LARGE_THUMB,
    FormatEnum.MED_THUMB,
    FormatEnum.SMALL_THUMB,
    FormatEnum.DASH,
)

# How much of the overall transcoding percentage each format is worth,
# roughly proportional to how long it actually takes to produce: pulling one
# thumbnail frame is over almost before it starts, next to a full two-pass
# video encode. Counting formats equally made the bar sit at a misleadingly
# large 50% for the entire duration of the real work.
FORMAT_WEIGHTS: dict[FormatEnum, int] = {
    FormatEnum.LARGE_THUMB: 1,
    FormatEnum.MED_THUMB: 1,
    FormatEnum.SMALL_THUMB: 1,
    FormatEnum.DASH: 4,
}


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

        async with self.archive.open() as archive:
            await self._archive_original(archive, video_id, original_file, metadata, reporter)

            with TemporaryDirectory(dir=self.work_dir, prefix=f"ingest-{video_id}-") as scratch:
                total_weight = sum(FORMAT_WEIGHTS[f] for f in DESIRED_FORMATS)
                completed_weight = 0
                for file_format in DESIRED_FORMATS:
                    # Progress through the transcoding state, counted in
                    # weighted finished formats to start with;
                    # _process_format refines this further using ffmpeg's
                    # own -progress output while that format is running.
                    await reporter.state(
                        IngestStateEnum.TRANSCODING,
                        percentage_done=round(100 * completed_weight / total_weight),
                    )
                    await self._process_format(
                        archive,
                        file_format,
                        metadata,
                        original_file,
                        video_id,
                        Path(scratch),
                        reporter,
                        completed_weight,
                        total_weight,
                    )
                    completed_weight += FORMAT_WEIGHTS[file_format]

        await self.django_api.set_video_proper_import(video_id, True)
        await reporter.state(IngestStateEnum.DONE, percentage_done=100)

        # Only now is the upload redundant. Leaving it in place on failure keeps
        # the file around for a retry; a re-upload of the same video clears it.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

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

            await self.django_api.create_video_file(
                filename=str(destination),
                file_format=FormatEnum.ORIGINAL,
                video_id=video_id,
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
        completed_weight: int,
        total_weight: int,
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

        template_args = ProfileTemplateArguments(
            input_file=source_file,
            output_file=output_file,
            output_dir=output_dir,
            scratch_dir=scratch,
            seek_s=(duration_s * 0.25 or 30),
            has_audio=any(stream.codec_type == "audio" for stream in metadata.streams or []),
        )

        command = template.render(template_args)
        self.logger.debug("Generated command: %s", command)

        try:
            await Task(
                command,
                duration_s=duration_s,
                passes=template.metadata.passes,
                on_progress=self._transcode_progress_reporter(
                    reporter, completed_weight, FORMAT_WEIGHTS[file_format], total_weight
                ),
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

    def _transcode_progress_reporter(
        self, reporter: IngestReporter, completed_weight: int, format_weight: int, total_weight: int
    ) -> Callable[[float], Awaitable[None]]:
        """Turns one format's ffmpeg progress into an overall percentage.

        Reports are throttled to one per whole percentage point: ffmpeg's
        -progress stream updates far more often than that, and each report
        is a call to django-api.
        """
        last_reported = -1

        async def report(fraction: float) -> None:
            nonlocal last_reported
            percentage = round(100 * (completed_weight + format_weight * fraction) / total_weight)
            if percentage != last_reported:
                last_reported = percentage
                await reporter.state(IngestStateEnum.TRANSCODING, percentage_done=percentage)

        return report
