"""What was observed about one video, in the terms the chores reason about.

Plain data, assembled once and then never consulted again -- a chore that went
back to the network could see a different archive halfway through deciding what
to do about it. Everything here is what was true when the video was looked at.

Reading a django-api row into these terms lives here too, because two callers
do it: a worker, which looks up one video, and the queue-side tools, which page
the whole catalogue. A row read two ways is a video the two disagree about.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum
from frikanalen_django_api_client.types import UNSET

from app.archive_store import ArchiveEntry
from app.formats import UNTRACKED_REVISION
from app.util.file_name_utils import IMAGES_DIR


def optional(value):
    """Unset and None both mean "django-api did not give us one"."""
    return None if value is UNSET else value


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

    @classmethod
    def from_row(cls, row) -> "RegisteredFile":
        return cls(
            id=row.id,
            variant=row.variant,
            filename=PurePosixPath(row.filename),
            profile_revision=_profile_revision(row),
            integrated_lufs=optional(row.integrated_lufs),
        )


def _profile_revision(row) -> int:
    """Which revision produced this file.

    The column is NOT NULL with a zero default, so every row has one; the
    fallback covers a row the API declined to serialize rather than a row that
    genuinely has no answer. Either way, nothing recorded reads as stale.
    """
    declared = optional(getattr(row, "profile_revision", None))
    return UNTRACKED_REVISION if declared is None else int(declared)


@dataclass(frozen=True)
class VideoState:
    """One video, as the database and the archive each describe it."""

    video_id: str
    files: tuple[RegisteredFile, ...] = ()
    #: Every directory directly under <id>/, and what is in it -- or None when
    #: the archive was never read. Present and empty means the directory exists
    #: and holds nothing, which is a different answer from nobody having
    #: looked: the queue-side tools decide what to queue from the catalogue
    #: alone, and a chore must not report media missing on the strength of a
    #: listing it never made.
    directories: Mapping[str, tuple[ArchiveEntry, ...]] | None = None
    duration: str | None = None
    framerate: int | None = None

    @classmethod
    def from_rows(
        cls,
        video_id: str,
        video,
        files: tuple[RegisteredFile, ...],
        directories: Mapping[str, tuple[ArchiveEntry, ...]] | None = None,
    ) -> "VideoState":
        """One video as django-api describes it, with the archive if it was read."""
        return cls(
            video_id=video_id,
            files=files,
            directories=directories,
            duration=optional(getattr(video, "duration", None)) if video else None,
            framerate=optional(getattr(video, "framerate", None)) if video else None,
        )

    @property
    def archive_was_read(self) -> bool:
        return self.directories is not None

    @property
    def has_archived_media(self) -> bool:
        """Whether any video media is archived for this video.

        Images do not count. They are registered against a different model, are
        never derived from an original, and a video whose only archived file is
        a piece of key art is not a video whose ladder went missing -- it is a
        video nobody has uploaded yet.

        A video whose archive was never read answers no, because every use of
        this is a claim about what is there that would be a lie unasked.
        """
        return any(self.contents_of(name) for name in (self.directories or {}) if name != IMAGES_DIR)

    def rows_for(self, variant: VideoFileVariantEnum) -> tuple[RegisteredFile, ...]:
        return tuple(f for f in self.files if f.variant == variant)

    def contents_of(self, directory: str) -> tuple[ArchiveEntry, ...]:
        """The files -- not the subdirectories -- directly inside `directory`."""
        return tuple(entry for entry in (self.directories or {}).get(directory, ()) if not entry.is_dir)

    def revision_of(self, variant: VideoFileVariantEnum) -> int:
        """The newest revision registered for `variant`.

        Newest rather than oldest: a format registered twice has been rebuilt,
        and it is the rebuild that describes what is in the archive now.
        """
        rows = self.rows_for(variant)
        return max((row.profile_revision for row in rows), default=UNTRACKED_REVISION)
