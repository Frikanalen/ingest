"""What a chore proposes doing to one video.

Actions are data, not closures: a plan can be printed, counted, diffed and
reviewed before anything happens, and reviewing it is the point -- one of them
takes a whole video out of the published tree.

Nothing here executes. app.backfill.apply does that.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import ClassVar

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.formats import UNTRACKED_REVISION


class Action:
    """One step toward the desired state.

    The two flags are class-level rather than fields: they describe what a kind
    of action is, not what a particular one happens to be, and keeping them off
    the dataclass lets each action declare its own arguments as required.
    """

    #: Whether carrying this out means having the source file locally. The
    #: distinction is worth several gigabytes a video: a run that only tidies
    #: directories should never pull originals off the archive to do it.
    needs_original: ClassVar[bool] = False
    #: Whether it takes something out of the published tree. Nothing here
    #: deletes -- destructive means "moves to .trash" -- but it is still the
    #: set a person should read before saying yes.
    destructive: ClassVar[bool] = False

    def describe(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class TrashPath(Action):
    """Take a path out of the published tree, recoverably."""

    path: PurePosixPath
    reason: str

    destructive: ClassVar[bool] = True

    def describe(self) -> str:
        return f"trash {self.path} ({self.reason})"


@dataclass(frozen=True)
class ProduceFormat(Action):
    """Build a derived format from the original and register it."""

    file_format: VideoFileVariantEnum
    to_revision: int
    #: What is registered now. UNTRACKED_REVISION covers both "nothing" and
    #: "made before we recorded this", which want the same treatment.
    from_revision: int = UNTRACKED_REVISION
    #: A stale format directory to swap out. Rebuilding replaces the directory
    #: rather than overwriting file by file, because a new revision can emit a
    #: different set of files and the old ones would otherwise linger beside
    #: the new output forever.
    replacing: PurePosixPath | None = None

    needs_original: ClassVar[bool] = True

    def describe(self) -> str:
        if self.from_revision != UNTRACKED_REVISION:
            return f"produce {self.file_format} (revision {self.from_revision} -> {self.to_revision})"
        if self.replacing is None:
            return f"produce {self.file_format} (missing)"
        return f"produce {self.file_format} (replacing output nothing claims)"


@dataclass(frozen=True)
class RefreshMetadata(Action):
    """Re-derive from the original what should have been recorded at upload."""

    #: Which of duration / framerate / loudness are missing. Named rather than
    #: inferred so a plan says what it is going to fill in.
    fields: tuple[str, ...]
    original_file_id: int

    needs_original: ClassVar[bool] = True

    def describe(self) -> str:
        return f"refresh {', '.join(self.fields)} from the original"
