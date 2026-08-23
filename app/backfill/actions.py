"""What a chore proposes doing to one video.

Actions are data, not closures: a plan can be printed, counted, diffed and
reviewed before anything happens, and reviewing it is the point -- some of
these move media around and one of them takes a whole video out of the
published tree.

Nothing here executes. app.backfill.apply does that.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import ClassVar

from app.django_client.service import FormatEnum
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
class MovePath(Action):
    """Relocate archived media without touching its bytes."""

    source: PurePosixPath
    destination: PurePosixPath

    def describe(self) -> str:
        return f"move {self.source} -> {self.destination}"


@dataclass(frozen=True)
class RetagFile(Action):
    """Point an existing videofile row at where its file now is.

    Updating a record we already have, rather than inventing one from a file we
    happened to find. The row keeps its identity and its history.
    """

    file_id: int
    variant: FormatEnum
    filename: PurePosixPath

    def describe(self) -> str:
        return f"retag videofile {self.file_id} as {self.variant} at {self.filename}"


@dataclass(frozen=True)
class UnregisterFile(Action):
    """Drop a videofile row for a file we are deliberately removing.

    Only ever paired with trashing the file it names. A row is never dropped
    because its file turned out to be missing -- that is an incident, and the
    row is the only remaining evidence of it.
    """

    file_id: int
    reason: str

    destructive: ClassVar[bool] = True

    def describe(self) -> str:
        return f"unregister videofile {self.file_id} ({self.reason})"


@dataclass(frozen=True)
class ProduceFormat(Action):
    """Build a derived format from the original and register it."""

    file_format: FormatEnum
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
        if self.replacing is None:
            return f"produce {self.file_format} (missing)"
        return f"produce {self.file_format} (revision {self.from_revision} -> {self.to_revision})"


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
