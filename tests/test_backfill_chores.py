"""What each chore decides, given what was observed.

Chores are pure, so the cases that matter -- a format built by a profile we
have moved past, media nothing claims, a video the catalogue has dropped -- are
ordinary table tests. No archive, no API, no ffmpeg.
"""

from pathlib import PurePosixPath

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.archive_store import ArchiveEntry
from app.backfill.actions import ProduceFormat, RefreshMetadata, TrashPath
from app.backfill.chores import DesiredState, plan
from app.backfill.state import RegisteredFile, VideoState
from app.formats import UNTRACKED_REVISION

VIDEO_ID = "12345"

#: Small and explicit, so a test says what it depends on rather than inheriting
#: whatever the shipped templates happen to declare today.
DESIRED = DesiredState(formats={VideoFileVariantEnum.DASH: 2, VideoFileVariantEnum.LARGE_THUMB: 1})


def entry(path: str, is_dir: bool = False, size: int = 1024) -> ArchiveEntry:
    return ArchiveEntry(path=PurePosixPath(path), is_dir=is_dir, size=size)


def registered(
    variant: VideoFileVariantEnum,
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
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=2, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
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


def test_a_deleted_videos_images_are_collected_with_everything_else():
    """Their rows went with the video row, so the bytes are garbage too."""
    state = video(
        in_catalogue=False,
        directories={
            "original": (entry(f"{VIDEO_ID}/original/source.mp4"),),
            "images": (entry(f"{VIDEO_ID}/images/2f92e90d.png"),),
        },
    )

    [action] = plan(state, DESIRED, chores=("gc",)).actions

    assert isinstance(action, TrashPath)
    assert action.path == PurePosixPath(VIDEO_ID)


def test_a_video_with_only_images_is_not_read_as_a_lost_ladder():
    """Key art uploaded before the video is the normal order of events."""
    state = video(files=(), directories={"images": (entry(f"{VIDEO_ID}/images/2f92e90d.png"),)})

    assert not plan(state, DESIRED, chores=("formats",)).notes


def test_a_format_directory_with_no_row_is_not_garbage():
    """It reads as missing to the formats chore, which rebuilds it."""
    state = video(files=tuple(f for f in video().files if f.variant != VideoFileVariantEnum.DASH))

    actions = actions_of(state)

    assert not any(isinstance(action, TrashPath) for action in actions)
    assert [a.file_format for a in actions if isinstance(a, ProduceFormat)] == [VideoFileVariantEnum.DASH]


# --- the legacy broadcast/ directory ---------------------------------------
#
# There is no chore for it any more. Settling which directory holds a video's
# source is a one-shot migration, run on the storage host by an operator as
# `fk-archive-migrate-broadcast` -- so what is tested here is that the backfill
# leaves such a video alone and says why, rather than what it does to it.


def broadcast_only(**overrides) -> VideoState:
    """The legacy shape: the source is there, under the name the old system used."""
    defaults = dict(
        files=(
            registered(VideoFileVariantEnum.BROADCAST, "source.mp4", file_id=7),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=2, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        ),
        directories={
            "broadcast": (entry(f"{VIDEO_ID}/broadcast/source.mp4"),),
            "dash": (entry(f"{VIDEO_ID}/dash/manifest.mpd"),),
            "large_thumb": (entry(f"{VIDEO_ID}/large_thumb/source.jpg"),),
        },
    )
    return video(**{**defaults, **overrides})


def test_a_video_whose_source_is_still_called_broadcast_is_left_alone():
    """Reported, not acted on. The backfill has no way to rename anything --
    deliberately -- so the honest answer is to say what is in the way and stop,
    rather than to derive formats from a source it cannot see."""
    result = plan(broadcast_only(), DESIRED)

    assert not result.actions
    assert any("no original is registered" in note for note in result.notes)


def test_a_video_with_no_source_at_all_is_silent():
    """Nothing archived and nothing registered is a video nobody has uploaded
    yet, not a ladder that went missing. There is nothing to report."""
    result = plan(video(files=(), directories={}), DESIRED)

    assert not result.actions
    assert not result.notes

# --- formats ---------------------------------------------------------------


def test_a_superseded_format_is_rebuilt_by_swapping_its_directory():
    state = video(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == VideoFileVariantEnum.DASH
    assert produce.from_revision == 1
    assert produce.to_revision == 2
    assert produce.replacing == PurePosixPath(f"{VIDEO_ID}/dash")


def test_a_file_registered_before_revisions_existed_is_stale():
    state = video(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=UNTRACKED_REVISION, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == VideoFileVariantEnum.DASH
    assert produce.from_revision == UNTRACKED_REVISION


def test_a_missing_format_is_produced_without_replacing_anything():
    state = video(
        files=tuple(f for f in video().files if f.variant != VideoFileVariantEnum.LARGE_THUMB),
        directories={k: v for k, v in video().directories.items() if k != "large_thumb"},
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == VideoFileVariantEnum.LARGE_THUMB
    assert produce.replacing is None


def test_a_rebuilt_format_takes_the_newest_revision_registered():
    """Registered twice means rebuilt, and the rebuild is what is there now."""
    state = video(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=2, file_id=4),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    assert not any(isinstance(a, ProduceFormat) for a in actions_of(state))


def test_a_registered_format_missing_from_the_archive_is_rebuilt_and_reported():
    state = video(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        ),
        directories={k: v for k, v in video().directories.items() if k != "dash"},
    )

    result = plan(state, DESIRED)

    [produce] = [a for a in result.actions if isinstance(a, ProduceFormat)]
    assert produce.replacing is None
    assert any("registered but missing" in note for note in result.notes)


def test_nothing_is_derived_without_a_registered_original():
    state = video(files=(registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=2, file_id=2),))

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


def unmeasured(**overrides) -> VideoState:
    """A video whose original carries no loudness figure."""
    return video(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", lufs=None, file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=2, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        ),
        **overrides,
    )


def test_a_missing_loudness_is_measured_alongside_a_refresh_that_fetches_anyway():
    """The fresh-upload path: the hook records a duration and nothing else, so
    every new video arrives needing its framerate, and the loudness costs a
    decode of audio that is local by then rather than a transfer of its own."""
    [refresh] = [a for a in actions_of(unmeasured(framerate=None)) if isinstance(a, RefreshMetadata)]

    assert refresh.fields == ("framerate", "loudness")
    assert refresh.original_file_id == 1


def test_a_missing_loudness_on_its_own_is_reported_rather_than_planned():
    """A silent original yields no measurement, so the column stays NULL and an
    action keyed on the column would be planned again on the run after this
    one, and the one after that -- each time fetching the whole original."""
    result = plan(unmeasured(), DESIRED)

    assert not result.actions
    assert any("no recorded loudness" in note for note in result.notes)


def test_an_unmeasurable_loudness_converges_after_one_pass():
    """The loop this closes: refreshing an original that turns out to be silent
    writes nothing, so the state it was keyed on comes back unchanged. The
    second pass has to stop asking, or it never will."""
    first = plan(unmeasured(framerate=None), DESIRED)
    assert first.needs_original

    # What the archive and the catalogue look like once that has been carried
    # out against a source with nothing to measure: framerate written, loudness
    # exactly as it was.
    second = plan(unmeasured(), DESIRED)

    assert not second.actions
    assert not second.needs_original


# --- the plan itself -------------------------------------------------------


def test_a_directory_only_plan_does_not_need_the_original():
    """Several gigabytes a video rides on this being right."""
    state = video(
        files=(*video().files, registered(VideoFileVariantEnum.BROADCAST, "source.mp4", file_id=7)),
        directories={**video().directories, "broadcast": (entry(f"{VIDEO_ID}/broadcast/source.mp4"),)},
    )

    assert not plan(state, DESIRED).needs_original


def test_a_plan_that_transcodes_needs_the_original():
    state = video(files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),))

    assert plan(state, DESIRED).needs_original


def test_chores_can_be_selected():
    state = video(in_catalogue=False)

    assert not plan(state, DESIRED, chores=("formats",)).actions
    assert plan(state, DESIRED, chores=("gc",)).actions


def test_a_plan_describes_itself():
    state = video(
        files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),),
        directories={"original": (entry(f"{VIDEO_ID}/original/source.mp4"),)},
    )

    described = plan(state, DESIRED).describe()

    assert f"video {VIDEO_ID}" in described
    assert "produce dash (missing)" in described


def test_output_nothing_claims_is_replaced_rather_than_collided_with():
    """A registration that failed after publishing leaves a complete directory
    with no row. Producing into it would hit put()'s refusal to overwrite and
    fail the same way on every retry, so the rebuild has to swap it."""
    state = video(
        files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),),
    )

    produced = {a.file_format: a for a in actions_of(state) if isinstance(a, ProduceFormat)}

    assert produced[VideoFileVariantEnum.DASH].replacing == PurePosixPath(f"{VIDEO_ID}/dash")
    assert "replacing output nothing claims" in produced[VideoFileVariantEnum.DASH].describe()


def test_a_format_absent_from_both_is_produced_without_replacing_anything():
    state = video(
        files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),),
        directories={"original": (entry(f"{VIDEO_ID}/original/source.mp4"),)},
    )

    produced = {a.file_format: a for a in actions_of(state) if isinstance(a, ProduceFormat)}

    assert produced[VideoFileVariantEnum.DASH].replacing is None
    assert "missing" in produced[VideoFileVariantEnum.DASH].describe()
