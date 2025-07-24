import logging
import shutil
from datetime import datetime
from pathlib import Path


from libraries.django_api.service import DjangoApiService
from libraries.tus_hook.hook_server import build_client


django_api = DjangoApiService(build_client())


async def handle_file(archive_path: Path, video_id: str, video_file: Path):
    logging.info("Handling file id: %d - moving from %s to %s", video_id, video_file.parent.absolute(), archive_path)

    archive_original_path = archive_path / video_id / "original" / video_file
    assert not archive_original_path.exists(), f"File {archive_original_path} already exists"

    archive_original_path.parent.mkdir(exist_ok=True)

    await django_api.set_video_uploaded_time(video_id, datetime.now())

    metadata = get_metadata(video_file)

    shutil.move(video_file, archive_original_path)
    await django_api.set_video_duration(video_id, metadata["pretty_duration"])

    await generate_videos(video_id, archive_original_path)
    await django_api.set_video_proper_import(video_id, True)


from .interactive import get_metadata, generate_videos
