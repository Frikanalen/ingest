import logging
import re
from pathlib import Path
from typing import TypedDict

from app.util.path_utils import get_single_file_from_directory

from .notify_runner import ingest

UPLOAD_DROP_DIR_REGEXP = r"^\d+$"


def is_valid_dropbox_dir(dirname: Path) -> bool:
    if not dirname.is_dir():
        logging.warning("Skipping %s because it is not a directory", dirname)
        return False

    if not re.match(UPLOAD_DROP_DIR_REGEXP, dirname.name):
        logging.warning("Skipping %s because it is hidden/not numeric", dirname)
        return False

    if not len(list(dirname.iterdir())) == 1:
        logging.warning("Skipping %s because it does not contain exactly one file", dirname)

    return True


PendingIngestJob = TypedDict("PendingIngestJob", {"video_file": Path, "video_id": str})


def run(input_base_path: Path, archive_path):
    for video_folder in filter(is_valid_dropbox_dir, input_base_path.iterdir()):
        video_file = get_single_file_from_directory(video_folder)
        video_id = video_folder.stem
        ingest(video_id, video_file)
