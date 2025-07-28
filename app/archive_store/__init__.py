import shutil
from logging import getLogger
from pathlib import Path

from app.util.file_name_utils import original_file_location
from app.util.settings import settings


class Archive:
    def __init__(self, archive_base_path: Path = Path(settings.archive_dir)):
        self.archive_base_path = archive_base_path
        self.logger = getLogger(__name__)

    def validate_destination(self, video_id: str, original_file_name: Path) -> Path:
        original_file_destination = self.archive_base_path / original_file_location(
            video_id, Path(original_file_name.name)
        )
        assert not original_file_destination.exists(), f"File {original_file_destination} already exists"

        self.logger.info("Destination: %s", original_file_destination)
        return original_file_destination

    def make_dir_for_original(self, original_file_name: Path):
        original_file_name.parent.mkdir(exist_ok=True, parents=True)
        self.logger.info("created destination folder %s", original_file_name.parent)
        return original_file_name

    def move_original_to_archive(self, video_id: str, original_file_name: Path):
        original_file_destination = self.validate_destination(video_id, original_file_name)
        self.logger.info("creating directory for original file: %s", original_file_destination)
        self.make_dir_for_original(original_file_destination)
        self.logger.info("moving original from %s to %s", original_file_name, original_file_destination)

        shutil.move(original_file_name, original_file_destination)
        return original_file_destination
