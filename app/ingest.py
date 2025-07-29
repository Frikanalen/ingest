from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path

from frikanalen_django_api_client.models import FormatEnum, VideoFileRequest

from app.django_client.service import DjangoApiService
from app.media.converter import Converter
from app.util.logging import VideoIdFilter

from .archive_store import Archive
from .media.comand_template import TemplatedCommandGenerator
from .media.ffprobe_schema import FfprobeOutput

DESIRED_FORMATS = (FormatEnum("large_thumb"),)


class Ingester:
    video_id: str
    django_api: DjangoApiService
    converter: Converter
    archive: Archive
    logger: Logger

    def __init__(self, django_api: DjangoApiService, converter: Converter, archive: Archive):
        self.logger = getLogger(__name__)
        self.django_api = django_api
        self.converter = converter
        self.archive = archive

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
            req = VideoFileRequest(filename=str(archive_original), format_=FormatEnum.ORIGINAL, video=int(video_id))
            await self.django_api.create_video_file(video_file=req)
        except Exception as e:
            self.logger.error("django-api error post original ingest: %s", e)
            raise

        for format in DESIRED_FORMATS:
            await self._process_format(format, metadata, archive_original, video_id)

        await self.django_api.set_video_proper_import(video_id, True)

    async def _process_format(self, format: FormatEnum, metadata: FfprobeOutput, archive_original: Path, video_id: str):
        self.logger.info("Processing: %s", archive_original)
        output_directory = archive_original.parent.parent / format

        self.logger.info("Creating directory: %s", output_directory)
        output_directory.mkdir(exist_ok=True)

        self.logger.info("Building command for format: %s", format)
        template = TemplatedCommandGenerator(format)

        output_file_name = f"{archive_original.stem}.{template.metadata.output_file_extension}"
        output_file = output_directory / output_file_name

        self.logger.info("Storing %s to %s", format, output_file)
        await self.converter.process_format(archive_original, output_file, template, metadata)

        self.logger.info("Creating video file entry for %s", output_file)
        req = VideoFileRequest(filename=str(output_file), format_=format, video=int(video_id))
        await self.django_api.create_video_file(video_file=req)
