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

        await self.django_api.set_video_uploaded_time(video_id, datetime.now())

        original_file_destination = self.archive.move_original_to_archive(video_id, original_file)
        await self.django_api.set_video_duration(video_id, metadata.format.duration)

        for format_name in DESIRED_FORMATS:
            self.logger.info("Processing: %s", original_file_destination)
            output_directory = original_file_destination.parent.parent / format_name
            output_directory.mkdir(exist_ok=True)

            template = TemplatedCommandGenerator(format_name)
            output_file_name = f"{original_file_destination.stem}.{template.metadata.output_file_extension}"
            output_file = output_directory / output_file_name
            self.logger.info("Producing: %s", format_name)

            await self.converter.process_format(original_file_destination, output_file, template, metadata)
            self.logger.info("Finished processing: %s", video_id)
            req = VideoFileRequest(filename=str(output_file), format_=format_name, video_id=int(video_id))
            await self.django_api.create_video_file(video_file=req)

        await self.django_api.set_video_proper_import(video_id, True)
