"""What each chore decides, given what was observed.

Chores are pure, so the cases that matter -- a broadcast-only video, a format
built by a profile we have moved past, media nothing claims -- are ordinary
table tests. No archive, no API, no ffmpeg.
"""

from pathlib import PurePosixPath

import pytest

from app.archive_store import ArchiveEntry
from app.backfill.actions import (
    MovePath,
    ProduceFormat,
    RefreshMetadata,
    RetagFile,
    TrashPath,
    UnregisterFile,
)
from app.backfill.chores import DesiredState, plan
from app.backfill.state import RegisteredFile, VideoState
from app.django_client.service import FormatEnum
from app.formats import UNTRACKED_REVISION

VIDEO_ID = "12345"

#: Small and explicit, so a test says what it depends on rather than inheriting
#: whatever the shipped templates happen to declare today.
DESIRED = DesiredState(formats={FormatEnum.DASH: 2, FormatEnum.LARGE_THUMB: 1})


def entry(path: str, is_dir: bool = False, size: int = 1024) -> ArchiveEntry:
    return ArchiveEntry(path=PurePosixPath(path), is_dir=is_dir, size=size)


def registered(
    variant: FormatEnum,
    name: str,
    revision: int = UNTRACKED_REVISION,
    lufs: float | None = -23.0,
    file_id: int = 1,
):
    return RegisteredFile(
        id=file_id,
        variant=variant,
        filename=PurePosixPath(f"{VIDEO_ID}/{variant}/{name}"),
        profile_revision=revision,
        integrated_lufs=lufs,
    )


def video(**overrides) -> VideoState:
    """A healthy, fully-derived video. Tests break exactly one thing."""
    base = dict(
        video_id=VIDEO_ID,
        in_catalogue=True,
        duration="00:10:00",
        framerate=25000,
        files=(
            registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(FormatEnum.DASH, "manifest.mpd", revision=2, file_id=2),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        ),
        directories={
            "original": (entry(f"{VIDEO_ID}/original/source.mp4"),),
            "dash": (entry(f"{VIDEO_ID}/dash/manifest.mpd"),),
            "large_thumb": (entry(f"{VIDEO_ID}/large_thumb/source.jpg"),),
        },
    )
    return VideoState(**{**base, **overrides})


def actions_of(state, **kwargs):
    return plan(state, DESIRED, **kwargs).actions


def test_a_healthy_video_needs_nothing():
    assert not plan(video(), DESIRED)


# --- garbage collection ----------------------------------------------------


def test_media_for_a_deleted_video_is_trashed():
    [action] = actions_of(video(in_catalogue=False))

    assert isinstance(action, TrashPath)
    assert action.path == PurePosixPath(VIDEO_ID)
    assert action.destructive


def test_a_deleted_video_with_no_media_needs_nothing():
    assert not plan(video(in_catalogue=False, directories={}), DESIRED)


def test_nothing_is_derived_for_a_video_being_collected():
    """Trashing it and then rebuilding its ladder would be an expensive loop."""
    actions = actions_of(video(in_catalogue=False, files=()))

    assert not any(isinstance(action, ProduceFormat) for action in actions)


def test_a_format_directory_with_no_row_is_not_garbage():
    """It reads as missing to the formats chore, which rebuilds it."""
    state = video(files=tuple(f for f in video().files if f.variant != FormatEnum.DASH))

    actions = actions_of(state)

    assert not any(isinstance(action, TrashPath) for action in actions)
    assert [a.file_format for a in actions if isinstance(a, ProduceFormat)] == [FormatEnum.DASH]


# --- original / broadcast --------------------------------------------------


def broadcast_only(**overrides) -> VideoState:
    """The legacy shape: the source is there, under the name the old system used."""
    defaults = dict(
        files=(
            registered(FormatEnum.BROADCAST, "source.mp4", file_id=7),
            registered(FormatEnum.DASH, "manifest.mpd", revision=2, file_id=2),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        ),
        directories={
            "broadcast": (entry(f"{VIDEO_ID}/broadcast/source.mp4"),),
            "dash": (entry(f"{VIDEO_ID}/dash/manifest.mpd"),),
            "large_thumb": (entry(f"{VIDEO_ID}/large_thumb/source.jpg"),),
        },
    )
    return video(**{**defaults, **overrides})


def test_broadcast_is_trashed_when_an_original_exists():
    state = video(
        files=(*video().files, registered(FormatEnum.BROADCAST, "source.mp4", file_id=7)),
        directories={**video().directories, "broadcast": (entry(f"{VIDEO_ID}/broadcast/source.mp4"),)},
    )

    actions = actions_of(state)

    [trash] = [a for a in actions if isinstance(a, TrashPath)]
    [unregister] = [a for a in actions if isinstance(a, UnregisterFile)]
    assert trash.path == PurePosixPath(f"{VIDEO_ID}/broadcast")
    assert unregister.file_id == 7


def test_broadcast_becomes_the_original_when_there_is_no_other():
    actions = actions_of(broadcast_only())

    [move] = [a for a in actions if isinstance(a, MovePath)]
    [retag] = [a for a in actions if isinstance(a, RetagFile)]
    assert move.source == PurePosixPath(f"{VIDEO_ID}/broadcast/source.mp4")
    assert move.destination == PurePosixPath(f"{VIDEO_ID}/original/source.mp4")
    assert retag.file_id == 7
    assert retag.variant == FormatEnum.ORIGINAL
    assert retag.filename == PurePosixPath(f"{VIDEO_ID}/original/source.mp4")


def test_the_emptied_broadcast_directory_is_trashed_last():
    actions = actions_of(broadcast_only())

    trashes = [i for i, a in enumerate(actions) if isinstance(a, TrashPath)]
    moves = [i for i, a in enumerate(actions) if isinstance(a, MovePath)]
    assert min(trashes) > max(moves)


def test_formats_are_planned_against_the_renamed_original():
    """The rename has not happened yet, but the formats chore must see it as
    though it had -- otherwise a broadcast-only video looks sourceless and
    every derivative it needs goes unplanned."""
    state = broadcast_only(
        files=(registered(FormatEnum.BROADCAST, "source.mp4", file_id=7),),
    )

    produced = [a.file_format for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert set(produced) == {FormatEnum.DASH, FormatEnum.LARGE_THUMB}


def test_unclaimed_broadcast_media_is_reported_not_moved():
    """Moving it would guess it is the source; registering it would invent a
    record from a file. Neither is ours to decide."""
    state = broadcast_only(files=())

    result = plan(state, DESIRED)

    assert not any(isinstance(a, MovePath) for a in result.actions)
    assert any("no videofile row" in note for note in result.notes)


def test_a_video_with_no_source_at_all_is_reported():
    state = video(files=(), directories={})

    result = plan(state, DESIRED)

    assert not result.actions
    assert any("nothing to derive from" in note for note in result.notes)


# --- formats ---------------------------------------------------------------


def test_a_superseded_format_is_rebuilt_by_swapping_its_directory():
    state = video(
        files=(
            registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(FormatEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == FormatEnum.DASH
    assert produce.from_revision == 1
    assert produce.to_revision == 2
    assert produce.replacing == PurePosixPath(f"{VIDEO_ID}/dash")


def test_a_file_registered_before_revisions_existed_is_stale():
    state = video(
        files=(
            registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(FormatEnum.DASH, "manifest.mpd", revision=UNTRACKED_REVISION, file_id=2),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == FormatEnum.DASH
    assert produce.from_revision == UNTRACKED_REVISION


def test_a_missing_format_is_produced_without_replacing_anything():
    state = video(
        files=tuple(f for f in video().files if f.variant != FormatEnum.LARGE_THUMB),
        directories={k: v for k, v in video().directories.items() if k != "large_thumb"},
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == FormatEnum.LARGE_THUMB
    assert produce.replacing is None


def test_a_rebuilt_format_takes_the_newest_revision_registered():
    """Registered twice means rebuilt, and the rebuild is what is there now."""
    state = video(
        files=(
            registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(FormatEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(FormatEnum.DASH, "manifest.mpd", revision=2, file_id=4),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    assert not any(isinstance(a, ProduceFormat) for a in actions_of(state))


def test_a_registered_format_missing_from_the_archive_is_rebuilt_and_reported():
    state = video(
        files=(
            registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(FormatEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        ),
        directories={k: v for k, v in video().directories.items() if k != "dash"},
    )

    result = plan(state, DESIRED)

    [produce] = [a for a in result.actions if isinstance(a, ProduceFormat)]
    assert produce.replacing is None
    assert any("registered but missing" in note for note in result.notes)


def test_nothing_is_derived_without_a_registered_original():
    state = video(files=(registered(FormatEnum.DASH, "manifest.mpd", revision=2, file_id=2),))

    result = plan(state, DESIRED)

    assert not any(isinstance(a, ProduceFormat) for a in result.actions)
    assert any("no original is registered" in note for note in result.notes)


# --- metadata --------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"duration": None}, "duration"),
        ({"framerate": None}, "framerate"),
        ({"framerate": 0}, "framerate"),
    ],
)
def test_missing_video_metadata_is_refreshed_from_the_original(override, expected):
    [refresh] = [a for a in actions_of(video(**override)) if isinstance(a, RefreshMetadata)]

    assert expected in refresh.fields
    assert refresh.original_file_id == 1


def test_a_missing_loudness_measurement_is_refreshed():
    state = video(
        files=(
            registered(FormatEnum.ORIGINAL, "source.mp4", lufs=None, file_id=1),
            registered(FormatEnum.DASH, "manifest.mpd", revision=2, file_id=2),
            registered(FormatEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    [refresh] = [a for a in actions_of(state) if isinstance(a, RefreshMetadata)]

    assert refresh.fields == ("loudness",)


# --- the plan itself -------------------------------------------------------


def test_a_directory_only_plan_does_not_need_the_original():
    """Several gigabytes a video rides on this being right."""
    state = video(
        files=(*video().files, registered(FormatEnum.BROADCAST, "source.mp4", file_id=7)),
        directories={**video().directories, "broadcast": (entry(f"{VIDEO_ID}/broadcast/source.mp4"),)},
    )

    assert not plan(state, DESIRED).needs_original


def test_a_plan_that_transcodes_needs_the_original():
    state = video(files=(registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),))

    assert plan(state, DESIRED).needs_original


def test_chores_can_be_selected():
    state = video(in_catalogue=False)

    assert not plan(state, DESIRED, chores=("formats",)).actions
    assert plan(state, DESIRED, chores=("gc",)).actions


def test_a_plan_describes_itself():
    state = video(files=(registered(FormatEnum.ORIGINAL, "source.mp4", file_id=1),))

    described = plan(state, DESIRED).describe()

    assert f"video {VIDEO_ID}" in described
    assert "produce dash (missing)" in described
