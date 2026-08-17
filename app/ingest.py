from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path
from tempfile import TemporaryDirectory

from app.django_client.service import DjangoApiService, FormatEnum
from app.util.file_name_utils import derived_file_location, original_file_location
from app.util.logging import VideoIdFilter

from .archive_store import ArchiveSession, ArchiveStore
from .media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from .media.ffprobe_schema import FfprobeOutput
from .runner import Task

DESIRED_FORMATS = (
    FormatEnum.LARGE_THUMB,
    FormatEnum.WEBM_MED,
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

    async def ingest(self, video_id: str, original_file: Path, metadata: FfprobeOutput):
        self.logger.addFilter(VideoIdFilter(video_id))
        self.logger.info("Ingesting file with video_id: %s, original_file: %s", video_id, original_file)

        try:
            await self.django_api.set_video_uploaded_time(video_id, datetime.now())
        except Exception as e:
            self.logger.error("Failed to set video uploaded time: %s", e)
            raise

        async with self.archive.open() as archive:
            await self._archive_original(archive, video_id, original_file, metadata)

            with TemporaryDirectory(dir=self.work_dir, prefix=f"ingest-{video_id}-") as scratch:
                for file_format in DESIRED_FORMATS:
                    await self._process_format(archive, file_format, metadata, original_file, video_id, Path(scratch))

        await self.django_api.set_video_proper_import(video_id, True)

        # Only now is the upload redundant. Leaving it in place on failure keeps
        # the file around for a retry; a re-upload of the same video clears it.
        self.logger.info("Removing uploaded file %s", original_file)
        original_file.unlink()

    async def _archive_original(
        self, archive: ArchiveSession, video_id: str, original_file: Path, metadata: FfprobeOutput
    ):
        destination = original_file_location(video_id, Path(original_file.name))

        try:
            self.logger.info("Storing original file at %s", destination)
            await archive.assert_absent(destination)
            await archive.put(original_file, destination)
        except Exception as e:
            self.logger.error("Failed to store original file in archive: %s", e)
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
            raise

    async def _process_format(
        self,
        archive: ArchiveSession,
        file_format: FormatEnum,
        metadata: FfprobeOutput,
        source_file: Path,
        video_id: str,
        scratch: Path,
    ):
        self.logger.info("Processing %s as %s", source_file, file_format)

        self.logger.info("Building command for format: %s", file_format)
        template = TemplatedCommandGenerator(file_format)

        output_file = scratch / f"{source_file.stem}.{template.metadata.output_file_extension}"

        template_args = ProfileTemplateArguments(
            input_file=source_file,
            output_file=output_file,
            seek_s=(float(metadata.format.duration) * 0.25 or 30),
        )

        command = template.render(template_args)
        self.logger.debug("Generated command: %s", command)

        await Task(command).execute()

        destination = derived_file_location(video_id, str(file_format), output_file)
        self.logger.info("Storing %s to %s", file_format, destination)
        await archive.put(output_file, destination)

        self.logger.info("Creating video file entry for %s", destination)
        await self.django_api.create_video_file(filename=str(destination), file_format=file_format, video_id=video_id)
