"""Deciding what one video needs, and nothing else.

Every chore is a pure function from an observed VideoState to the actions that
would bring it toward the desired state. No network, no filesystem, no clock --
which is what makes the interesting cases (an original that is registered but
missing, a format built by a superseded profile, media nothing claims) table
tests rather than fixtures.

Chores run in a fixed order and each is handed the state its predecessors will
have left behind, so the metadata a probe fills in is already there when the
formats derived from it are planned.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.converge.actions import Action, ProduceFormat, RefreshMetadata, RetirePreview
from app.converge.state import VideoState
from app.formats import DASH_PREVIEW, DESIRED_FORMATS, current_revision

ORIGINAL_DIR = "original"


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

    def describe(self) -> str:
        lines = [f"video {self.video_id}"]
        lines += [f"  - {action.describe()}" for action in self.actions]
        lines += [f"  ? {note}" for note in self.notes]
        return "\n".join(lines)


def refresh_metadata(state: VideoState, desired: DesiredState) -> Fragment:
    """Fill in what the database should have learned from the file at upload.

    Anything uploaded before ingest knew to record these has them empty, and
    framerate is empty everywhere because nothing has ever been able to write
    it.

    Duration and framerate fall out of a probe, so asking for them is asking
    for something that will definitely be written. Loudness is not like that.
    A silent track, or one loudnorm reports as -inf, has no measurement to
    record, and `integrated_lufs` stays NULL however many times it is measured
    -- the column cannot say "measured, and there was nothing to measure".

    So loudness is not on its own a reason to fetch anything. Keyed on the
    column alone this chore would plan a full original transfer, a probe and a
    decode of the audio, per affected video, on every single run, for ever,
    and write nothing each time. Instead loudness rides along with a refresh
    that was going to fetch the original anyway, and is otherwise reported.

    That costs the measurement only where the video needs nothing else, which
    in practice is the set that has already been through one backfill: the
    hook records a duration and nothing else, so every video arrives missing
    its framerate and is measured on its first pass. What is left over
    afterwards is very nearly exactly the videos that cannot be measured.
    Distinguishing the remainder properly needs django-api to record that a
    measurement was attempted; there is no field for it today.
    """
    originals = state.rows_for(VideoFileVariantEnum.ORIGINAL)
    if not originals:
        return Fragment(state)

    original = originals[0]
    probed = tuple(
        name
        for name, absent in (
            ("duration", not state.duration),
            ("framerate", not state.framerate),
        )
        if absent
    )
    unmeasured = original.integrated_lufs is None

    if probed:
        # The original is coming down for the probe regardless, and measuring
        # it is a decode of audio that is already local by then, so asking for
        # the loudness here is free.
        return Fragment(
            state,
            actions=(
                RefreshMetadata(
                    fields=probed + (("loudness",) if unmeasured else ()),
                    original_file_id=original.id,
                ),
            ),
        )

    if unmeasured:
        return Fragment(
            state,
            notes=(
                "the original has no recorded loudness; measuring it would mean fetching a file "
                "nothing else needs, and a track with nothing to measure would leave the column "
                "as it found it and be asked for again on every run",
            ),
        )

    return Fragment(state)


def produce_formats(state: VideoState, desired: DesiredState) -> Fragment:
    """Build every desired format that is absent or built by an older profile."""
    if not state.rows_for(VideoFileVariantEnum.ORIGINAL):
        # Something is registered against this video, or something is archived
        # for it, but the one file everything is derived from is neither.
        # Usually the legacy shape: the source is still under `broadcast/`,
        # waiting on the one-shot migration on the storage host. Said out loud
        # rather than repaired -- nothing here renames anything.
        #
        # The rows are asked first because they are the half a caller that
        # never read the archive still has, and this is the note worth keeping
        # for it: it counts the videos the migration has yet to reach.
        if state.files or state.has_archived_media:
            return Fragment(state, notes=("no original is registered; nothing can be derived",))
        return Fragment(state)

    actions: list[Action] = []
    notes: list[str] = []

    #: Whether this video ends the plan with a current ladder -- because it
    #: already had one, or because this plan builds it. Only then is there
    #: anything for a preview to be superseded by.
    dash_settled = False
    planned_preview = False

    for file_format, wanted in desired.formats.items():
        directory = PurePosixPath(state.video_id) / str(file_format)
        registered = state.rows_for(file_format)
        have = state.revision_of(file_format)

        if registered and have >= wanted:
            dash_settled = dash_settled or file_format == VideoFileVariantEnum.DASH
            continue

        # Whether something is in the way is a question about the archive, not
        # about the rows. A registration that failed after its files were
        # published leaves a complete directory nothing claims, and publishing
        # into it would collide rather than replace -- put() refuses to
        # overwrite, by design. So the swap is keyed off what is actually
        # there, and off nothing at all when nobody has looked: whoever is
        # deciding what to queue plans from the catalogue, and the worker that
        # claims the video plans it again with the archive in front of it.
        occupied = state.archive_was_read and str(file_format) in state.directories

        if registered and state.archive_was_read and not occupied:
            # Registered, due a rebuild, and not actually there. Rebuilding is
            # still right, but the absence is worth saying out loud.
            notes.append(f"{directory} is registered but missing from the archive")

        if file_format == VideoFileVariantEnum.DASH:
            dash_settled = True
            if not registered:
                # No ladder is registered at any revision, so there is nothing
                # this video can currently be watched with, and the one being
                # planned below is hours away. A rebuild is deliberately not
                # this case: a stale ladder still plays, and replacing it with
                # a preview for the length of an encode would be a downgrade
                # for a video people are already watching.
                #
                # Inserted here rather than by a chore of its own so it lands
                # where it belongs in the order -- after the thumbnails, which
                # are seconds, and before the ladder, which is hours.
                actions.append(
                    ProduceFormat(
                        file_format=DASH_PREVIEW,
                        to_revision=current_revision(DASH_PREVIEW),
                        # The ladder below owns the bar. Two encodes reporting
                        # into it would run it to 100 and start it again, which
                        # to a member watching is an import that restarted.
                        drives_progress=False,
                    )
                )
                planned_preview = True

        actions.append(
            ProduceFormat(
                file_format=file_format,
                from_revision=have,
                to_revision=wanted,
                replacing=directory if occupied else None,
            )
        )

    if dash_settled and (planned_preview or state.rows_for(DASH_PREVIEW)):
        # Last, so that a ladder which fails takes the plan down before the
        # thing standing in for it is destroyed. `state.rows_for` covers the
        # retry: a run that published the ladder and then failed to delete the
        # preview leaves a row this finds on the next pass, with no ladder work
        # left to do and so nothing to fetch.
        actions.append(
            RetirePreview(
                video_id=state.video_id,
                directory=PurePosixPath(state.video_id) / DASH_PREVIEW,
            )
        )

    return Fragment(state, actions=tuple(actions), notes=tuple(notes))


Chore = Callable[[VideoState, DesiredState], Fragment]

#: In order, and this is the whole set: what it means to converge one video,
#: and therefore what every caller does -- a worker draining the queue, and the
#: terminal deciding what to put on it. There is deliberately no second list
#: for one of them to run instead, because a chore that reached one path and
#: not the other is precisely the drift this arrangement exists to prevent.
#:
#: Metadata before formats: a format built from an original nobody has probed
#: is a format built at the wrong frame rate.
CHORES: Mapping[str, Chore] = {
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
