"""What a chore proposes doing to one video.

Actions are data, not closures: a plan can be printed, counted, diffed and
reviewed before anything happens, and reviewing a whole-catalogue run before
it is queued is the point.

Nothing here executes. app.converge.apply does that.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.formats import UNTRACKED_REVISION


class Action:
    """One step toward the desired state.

    Most of them derive something from the video's source file, which is why
    `Applier` fetches it once for any plan that needs it at all. `needs_source`
    is what "at all" means: an action that says no is one a plan can be made
    entirely of without a multi-gigabyte transfer nothing reads.
    """

    #: Whether carrying this out requires the video's original. Only false for
    #: an action whose whole subject is media the archive already holds.
    needs_source: bool = True

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


@dataclass(frozen=True)
class RetirePreview(Action):
    """Destroy the preview, now that the ladder it stood in for is registered.

    Destroyed rather than trashed, which is the one place this codebase does
    that. `.trash/` exists so an operator can look at what was removed before
    `fk-archive-purge-trash` finishes the job, and a preview per upload would
    bury the things that reading is for. It is safe to make an exception of
    because a preview is regenerable from the original and was built to be
    thrown away -- see `app.formats.DASH_PREVIEW`.

    Carries no row id. The preview it retires may have been registered minutes
    earlier by the same plan, whose row id nothing here could have known when
    the plan was made, so the applier reads the rows back instead.

    Planned last, and only ever after the `dash` that supersedes it. `Applier`
    runs actions in order and lets exceptions through, so a ladder that fails
    stops the plan before this runs and the preview survives exactly the
    failure it exists for.
    """

    video_id: str
    #: Where the preview lives, for the log. The archive is told the variant
    #: and the video id, not a path.
    directory: PurePosixPath

    needs_source = False

    def describe(self) -> str:
        return f"retire {self.directory}, now superseded by dash"
