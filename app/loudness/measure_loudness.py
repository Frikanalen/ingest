import logging
import os

from app.django_api.service import DjangoApiService
from app.loudness.get_loudness import get_loudness


async def run_missing_loudness_measurements(move_to_dir):
    """Measure loudness for all video files missing it, a few at the time.

    Process latest videos first.
    """
    django_api = DjangoApiService()
    video_files = await django_api.get_original_files_without_loudness()

    logging.info("found %d files with outstanding loudness measurements", video_files.count)

    for video_file in video_files:
        if loudness := get_loudness(os.path.join(move_to_dir, video_file["filename"])):
            await django_api.set_video_loudness(video_file["video_id"], loudness)
