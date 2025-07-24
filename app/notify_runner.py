import logging
import shutil
from datetime import datetime
from pathlib import Path

from app.django_api.service import DjangoApiService
from app.tus_hook.hook_server import build_client

from .ffprobe.probe import ffprobe_file
from .interactive import generate_videos
from .logging.video_id_filter import VideoIdFilter
from .util.file_name_utils import original_file_location

django_api = DjangoApiService(build_client())
archive_base_path = Path("/tmp/archive")


async def ingest(video_id: str, original_file: Path):
    logger = logging.getLogger(__name__)
    logger.addFilter(VideoIdFilter(video_id))
    logger.info("Ingesting file with video_id: %s, original_file: %s", video_id, original_file)

    original_file_destination = archive_base_path / original_file_location(video_id, original_file)
    assert not original_file_destination.exists(), f"File {original_file_destination} already exists"
    logger.info("Destination: %s", original_file_destination)

    logger.info("Creating destination folder: %s", original_file_destination.parent)
    original_file_destination.parent.mkdir(exist_ok=True)

    await django_api.set_video_uploaded_time(video_id, datetime.now())

    metadata = await ffprobe_file(original_file)
    logger.info("Got metadata: %s", metadata)

    logger.info("Moving from %s to %s", original_file, original_file_destination)
    shutil.move(original_file, original_file_destination)

    await django_api.set_video_duration(video_id, metadata["format"]["duration"])

    await generate_videos(video_id, original_file_destination)
    await django_api.set_video_proper_import(video_id, True)
