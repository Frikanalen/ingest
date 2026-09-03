"""What a chore proposes doing to one video.

Actions are data, not closures: a plan can be printed, counted, diffed and
reviewed before anything happens, and reviewing a whole-catalogue run before
it is queued is the point.

Nothing here executes. app.backfill.apply does that.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.formats import UNTRACKED_REVISION


class Action:
    """One step toward the desired state.

    There used to be a `needs_original` flag here, so that a plan which only
    tidied directories did not pull gigabytes off the archive to do it. Every
    remaining action derives something from the source file, so the flag was
    true of all of them and decided nothing; `Applier` fetches once, for any
    plan that has work in it. Reinstate the distinction if an action that does
    not need the original ever comes back.
    """

    def describe(self) -> str:
        raise NotImplementedError


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

    def describe(self) -> str:
        return f"refresh {', '.join(self.fields)} from the original"
