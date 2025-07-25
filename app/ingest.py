from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path

from frikanalen_django_api_client.models import FormatEnum

from app.django_api.service import DjangoApiService
from app.ffprobe import do_probe

from .archive import Archive
from .converter import Converter
from .logging.video_id_filter import VideoIdFilter

DESIRED_FORMATS = (FormatEnum("large_thumb"),)


class Ingester:
    video_id: str
    django_api: DjangoApiService
    converter_service: Converter
    archive: Archive
    logger: Logger

    def __init__(self, video_id: str, django_api: DjangoApiService, converter_service: Converter, archive: Archive):
        self.logger = getLogger(__name__)
        self.logger.addFilter(VideoIdFilter(video_id))
        self.video_id = video_id
        self.django_api = django_api
        self.converter_service = converter_service
        self.archive = archive

    async def ingest(self, original_file: Path):
        self.logger.info("Ingesting file with video_id: %s, original_file: %s", self.video_id, original_file)

        await self.django_api.set_video_uploaded_time(self.video_id, datetime.now())
        metadata = await do_probe(original_file)
        self.logger.info("Got metadata, %d streams", len(metadata.streams))

        original_file_destination = self.archive.move_original_to_archive(self.video_id, original_file)
        await self.django_api.set_video_duration(self.video_id, metadata.format.duration)

        for format_name in DESIRED_FORMATS:
            self.logger.info("Processing: %s", original_file_destination)
            await self.converter_service.process_format(
                original_file_destination, format_name, metadata, int(self.video_id)
            )
            self.logger.info("Finished processing: %s", self.video_id)

        await self.django_api.set_video_proper_import(self.video_id, True)
