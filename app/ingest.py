from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path

from app.django_client.service import DjangoApiService, FormatEnum
from app.util.logging import VideoIdFilter

from .archive_store import Archive
from .media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from .media.ffprobe_schema import FfprobeOutput
from .task_builder import TKB

DESIRED_FORMATS = (FormatEnum("large_thumb"),)


class Ingester:
    video_id: str
    django_api: DjangoApiService
    archive: Archive
    logger: Logger

    def __init__(
        self,
        archive_base_path: Path,
        django_api: DjangoApiService,
    ):
        self.logger = getLogger(__name__)
        self.archive = Archive(archive_base_path)
        self.django_api = django_api

    async def ingest(self, video_id: str, original_file: Path, metadata: FfprobeOutput):
        self.logger.addFilter(VideoIdFilter(video_id))
        self.logger.info("Ingesting file with video_id: %s, original_file: %s", video_id, original_file)

        try:
            await self.django_api.set_video_uploaded_time(video_id, datetime.now())
        except Exception as e:
            self.logger.error("Failed to set video uploaded time: %s", e)
            raise

        try:
            self.logger.info("Moving original file to archive")
            archive_original = self.archive.move_original_to_archive(video_id, original_file)
        except Exception as e:
            self.logger.error("Failed to move original file to archive: %s", e)
            raise

        try:
            self.logger.info("Setting video duration: %s", metadata.format.duration)
            await self.django_api.set_video_duration(video_id, metadata.format.duration)

            await self.django_api.create_video_file(
                filename=archive_original, file_format=FormatEnum.ORIGINAL, video_id=video_id
            )
        except Exception as e:
            self.logger.error("django-api error post original ingest: %s", e)
            raise

        for file_format in DESIRED_FORMATS:
            await self._process_format(file_format, metadata, archive_original, video_id)

        await self.django_api.set_video_proper_import(video_id, True)

    async def _process_format(
        self, file_format: FormatEnum, metadata: FfprobeOutput, archive_original: Path, video_id: str
    ):
        self.logger.info("Processing: %s", archive_original)
        output_directory = archive_original.parent.parent / file_format

        self.logger.info("Creating directory: %s", output_directory)
        output_directory.mkdir(exist_ok=True)

        self.logger.info("Building command for format: %s", file_format)
        template = TemplatedCommandGenerator(file_format)

        output_file_name = f"{archive_original.stem}.{template.metadata.output_file_extension}"
        output_file = output_directory / output_file_name

        self.logger.info("Storing %s to %s", file_format, output_file)

        template_args = ProfileTemplateArguments(
            input_file=archive_original,
            output_file=output_file,
            seek_s=(float(metadata.format.duration) * 0.25 or 30),
        )

        command = template.render(template_args)
        await TKB(command).execute()

        self.logger.info("Creating video file entry for %s", output_file)
        await self.django_api.create_video_file(filename=str(output_file), file_format=file_format, video_id=video_id)
