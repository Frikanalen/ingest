"""What was observed about one video, in the terms the chores reason about.

Plain data, assembled once and then never consulted again -- a chore that went
back to the network could see a different archive halfway through deciding what
to do about it. Everything here is what was true when the video was looked at.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.archive_store import ArchiveEntry
from app.formats import UNTRACKED_REVISION
from app.util.file_name_utils import IMAGES_DIR


@dataclass(frozen=True)
class RegisteredFile:
    """A videofile row: what django-api says this video has."""

    id: int
    variant: VideoFileVariantEnum
    filename: PurePosixPath
    #: Which iteration of the template produced it. UNTRACKED_REVISION means
    #: the row predates revisions being recorded, which reads as stale.
    profile_revision: int = UNTRACKED_REVISION
    integrated_lufs: float | None = None


@dataclass(frozen=True)
class VideoState:
    """One video, as the database and the archive each describe it."""

    video_id: str
    #: Whether django-api has a video with this id at all. False is what makes
    #: a directory in the archive garbage.
    in_catalogue: bool
    files: tuple[RegisteredFile, ...] = ()
    #: Every directory directly under <id>/, and what is in it. Present even
    #: when empty, so "the directory exists but holds nothing" stays
    #: distinguishable from "there is no such directory".
    directories: Mapping[str, tuple[ArchiveEntry, ...]] = field(default_factory=dict)
    duration: str | None = None
    framerate: int | None = None

    @property
    def has_archived_media(self) -> bool:
        """Whether any video media is archived for this video.

        Images do not count. They are registered against a different model, are
        never derived from an original, and a video whose only archived file is
        a piece of key art is not a video whose ladder went missing -- it is a
        video nobody has uploaded yet.
        """
        return any(self.contents_of(name) for name in self.directories if name != IMAGES_DIR)

    def rows_for(self, variant: VideoFileVariantEnum) -> tuple[RegisteredFile, ...]:
        return tuple(f for f in self.files if f.variant == variant)

    def contents_of(self, directory: str) -> tuple[ArchiveEntry, ...]:
        """The files -- not the subdirectories -- directly inside `directory`."""
        return tuple(entry for entry in self.directories.get(directory, ()) if not entry.is_dir)

    def revision_of(self, variant: VideoFileVariantEnum) -> int:
        """The newest revision registered for `variant`.

        Newest rather than oldest: a format registered twice has been rebuilt,
        and it is the rebuild that describes what is in the archive now.
        """
        rows = self.rows_for(variant)
        return max((row.profile_revision for row in rows), default=UNTRACKED_REVISION)

    def without_directory(self, directory: str) -> "VideoState":
        """This video as it will be once `directory` has been trashed."""
        return replace(self, directories={k: v for k, v in self.directories.items() if k != directory})

    def with_directory(self, directory: str, contents: tuple[ArchiveEntry, ...]) -> "VideoState":
        return replace(self, directories={**self.directories, directory: contents})
