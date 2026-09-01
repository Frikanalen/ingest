"""Deciding what one video needs, and nothing else.

Every chore is a pure function from an observed VideoState to the actions that
would bring it toward the desired state. No network, no filesystem, no clock --
which is what makes the interesting cases (a broadcast-only video, an original
that is registered but missing, a format built by a superseded profile) table
tests rather than fixtures.

Chores run in a fixed order and each is handed the state its predecessors will
have left behind, so a video whose original is still called broadcast/ can plan
its rename and the formats derived from the result in a single pass.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.backfill.actions import (
    Action,
    MovePath,
    ProduceFormat,
    RefreshMetadata,
    RetagFile,
    TrashPath,
    UnregisterFile,
)
from app.backfill.state import VideoState
from app.formats import DESIRED_FORMATS, current_revision

ORIGINAL_DIR = "original"
BROADCAST_DIR = "broadcast"


@dataclass(frozen=True)
class DesiredState:
    """What every video is supposed to have, and at which revision."""

    formats: Mapping[VideoFileVariantEnum, int]

    @classmethod
    def from_templates(cls) -> "DesiredState":
        return cls(formats={file_format: current_revision(file_format) for file_format in DESIRED_FORMATS})


@dataclass(frozen=True)
class Fragment:
    """One chore's contribution: what to do, what to say, and what that leaves."""

    state: VideoState
    actions: tuple[Action, ...] = ()
    #: Things worth telling a person that no chore will act on -- a row whose
    #: file is missing, media with no row. Reported, never repaired: inferring
    #: a record from a file, or deleting a record because a file is absent, are
    #: both ways of letting the archive overrule the database.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """Everything one video needs, in the order it should happen."""

    video_id: str
    actions: tuple[Action, ...] = ()
    notes: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.actions)

    @property
    def needs_original(self) -> bool:
        """Whether carrying this out means fetching the source file."""
        return any(action.needs_original for action in self.actions)

    @property
    def is_destructive(self) -> bool:
        return any(action.destructive for action in self.actions)

    def describe(self) -> str:
        lines = [f"video {self.video_id}"]
        lines += [f"  - {action.describe()}" for action in self.actions]
        lines += [f"  ? {note}" for note in self.notes]
        return "\n".join(lines)


def collect_garbage(state: VideoState, desired: DesiredState) -> Fragment:
    """Reclaim the media behind a video the catalogue no longer has.

    Whole videos only. A format directory with no row is left to the formats
    chore, which reads it as missing and rebuilds it -- collecting it here
    instead would have the two fighting over the same path.
    """
    if state.in_catalogue or not state.directories:
        return Fragment(state)

    return Fragment(
        state=replace(state, directories={}),
        actions=(
            TrashPath(
                path=PurePosixPath(state.video_id),
                reason="no video with this id in the catalogue",
            ),
        ),
    )


def reconcile_sources(state: VideoState, desired: DesiredState) -> Fragment:
    """Settle which directory holds the source, original/ or broadcast/."""
    if not state.in_catalogue:
        return Fragment(state)

    original = state.contents_of(ORIGINAL_DIR)
    broadcast = state.contents_of(BROADCAST_DIR)
    broadcast_dir = PurePosixPath(state.video_id) / BROADCAST_DIR
    rows = state.rows_for(VideoFileVariantEnum.BROADCAST)

    if not broadcast:
        if not original:
            return Fragment(state, notes=("no original and no broadcast: nothing to derive from",))
        return Fragment(state)

    if original:
        # Both present. The original is the one that is supposed to be there,
        # so the broadcast copy is redundant weight rather than a second source.
        return Fragment(
            state=replace(
                state.without_directory(BROADCAST_DIR),
                files=tuple(row for row in state.files if row.variant != VideoFileVariantEnum.BROADCAST),
            ),
            actions=(
                TrashPath(path=broadcast_dir, reason="original/ already holds the source"),
                *(UnregisterFile(file_id=row.id, reason="its broadcast/ file has been trashed") for row in rows),
            ),
        )

    if not rows:
        # Media with nothing claiming it. Moving it would be guessing that it
        # is this video's source, and registering it would be inventing a
        # record from a file -- so say so and leave it where it is.
        return Fragment(state, notes=(f"{broadcast_dir} holds media with no videofile row; left alone",))

    # Broadcast only, and something claims it: this is the source, under the
    # name the old system gave it. Moved file by file rather than as a
    # directory, so an empty original/ left behind by something else is not in
    # the way.
    moved = tuple(replace(entry, path=PurePosixPath(state.video_id) / ORIGINAL_DIR / entry.name) for entry in broadcast)

    # The rows are projected too, not just the directories. A chore that moved
    # the media but left the state calling it "broadcast" would leave the next
    # chore looking at a video with no registered original -- which reads as
    # "nothing can be derived", and would silently skip every format the video
    # is missing.
    retagged = tuple(
        replace(
            row,
            variant=VideoFileVariantEnum.ORIGINAL,
            filename=PurePosixPath(state.video_id) / ORIGINAL_DIR / row.filename.name,
        )
        if row.variant == VideoFileVariantEnum.BROADCAST
        else row
        for row in state.files
    )

    return Fragment(
        state=replace(
            state.without_directory(BROADCAST_DIR).with_directory(ORIGINAL_DIR, moved),
            files=retagged,
        ),
        actions=(
            *(MovePath(source=was.path, destination=now.path) for was, now in zip(broadcast, moved, strict=True)),
            *(
                RetagFile(
                    file_id=row.id,
                    variant=VideoFileVariantEnum.ORIGINAL,
                    filename=PurePosixPath(state.video_id) / ORIGINAL_DIR / row.filename.name,
                )
                for row in rows
            ),
            TrashPath(path=broadcast_dir, reason="emptied by the move to original/"),
        ),
    )


def refresh_metadata(state: VideoState, desired: DesiredState) -> Fragment:
    """Fill in what the database should have learned from the file at upload.

    Anything uploaded before ingest knew to record these has them empty, and
    framerate is empty everywhere because nothing has ever been able to write
    it.
    """
    if not state.in_catalogue:
        return Fragment(state)

    originals = state.rows_for(VideoFileVariantEnum.ORIGINAL)
    if not originals:
        return Fragment(state)

    original = originals[0]
    missing = tuple(
        name
        for name, absent in (
            ("duration", not state.duration),
            ("framerate", not state.framerate),
            ("loudness", original.integrated_lufs is None),
        )
        if absent
    )

    if not missing:
        return Fragment(state)

    return Fragment(state, actions=(RefreshMetadata(fields=missing, original_file_id=original.id),))


def produce_formats(state: VideoState, desired: DesiredState) -> Fragment:
    """Build every desired format that is absent or built by an older profile."""
    if not state.in_catalogue:
        return Fragment(state)

    if not state.rows_for(VideoFileVariantEnum.ORIGINAL):
        if state.has_archived_media:
            return Fragment(state, notes=("no original is registered; nothing can be derived",))
        return Fragment(state)

    actions: list[Action] = []
    notes: list[str] = []

    for file_format, wanted in desired.formats.items():
        directory = PurePosixPath(state.video_id) / str(file_format)
        registered = state.rows_for(file_format)
        have = state.revision_of(file_format)

        if registered and have >= wanted:
            continue

        # Whether something is in the way is a question about the archive, not
        # about the rows. A registration that failed after its files were
        # published leaves a complete directory nothing claims, and publishing
        # into it would collide rather than replace -- put() refuses to
        # overwrite, by design. So the swap is keyed off what is actually
        # there.
        occupied = str(file_format) in state.directories

        if registered and not occupied:
            # Registered, due a rebuild, and not actually there. Rebuilding is
            # still right, but the absence is worth saying out loud.
            notes.append(f"{directory} is registered but missing from the archive")

        actions.append(
            ProduceFormat(
                file_format=file_format,
                from_revision=have,
                to_revision=wanted,
                replacing=directory if occupied else None,
            )
        )

    return Fragment(state, actions=tuple(actions), notes=tuple(notes))


Chore = Callable[[VideoState, DesiredState], Fragment]

#: In order. Garbage first so nothing downstream spends an hour of CPU on a
#: video that is about to go; sources next, because it is what guarantees an
#: original exists for the last two to work from.
CHORES: Mapping[str, Chore] = {
    "gc": collect_garbage,
    "sources": reconcile_sources,
    "metadata": refresh_metadata,
    "formats": produce_formats,
}


def plan(state: VideoState, desired: DesiredState, chores: Sequence[str] = tuple(CHORES)) -> Plan:
    """Work out everything one video needs, running the chores in order."""
    actions: list[Action] = []
    notes: list[str] = []

    for name in chores:
        fragment = CHORES[name](state, desired)
        actions.extend(fragment.actions)
        notes.extend(fragment.notes)
        # The next chore sees the archive as this one will have left it.
        state = fragment.state

    return Plan(video_id=state.video_id, actions=tuple(actions), notes=tuple(notes))
