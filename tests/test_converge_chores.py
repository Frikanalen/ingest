"""What each chore decides, given what was observed.

Chores are pure, so the cases that matter -- a format built by a profile we
have moved past, media nothing claims, an original that is registered but
missing -- are ordinary table tests. No archive, no API, no ffmpeg.
"""

from pathlib import PurePosixPath

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.archive_store import ArchiveEntry
from app.converge.actions import ProduceFormat, RefreshMetadata, RetirePreview
from app.converge.chores import DesiredState, plan
from app.converge.state import RegisteredFile, VideoState
from app.formats import DASH_PREVIEW, UNTRACKED_REVISION

VIDEO_ID = "12345"

#: Small and explicit, so a test says what it depends on rather than inheriting
#: whatever the shipped templates happen to declare today.
DESIRED = DesiredState(formats={VideoFileVariantEnum.DASH: 2, VideoFileVariantEnum.LARGE_THUMB: 1})


def entry(path: str, is_dir: bool = False) -> ArchiveEntry:
    return ArchiveEntry(path=PurePosixPath(path), is_dir=is_dir)


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


# --- videos with nothing to derive from ------------------------------------


def test_a_video_with_only_images_is_not_read_as_a_lost_ladder():
    """Key art uploaded before the video is the normal order of events."""
    state = video(files=(), directories={"images": (entry(f"{VIDEO_ID}/images/2f92e90d.png"),)})

    assert not plan(state, DESIRED, chores=("formats",)).notes



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


# --- planning without having read the archive ------------------------------
#
# What the queue-side tools do. They decide from the catalogue alone, so every
# state they build says the archive was never looked at -- which has to be a
# different answer from having looked and found nothing, or every stale format
# in the catalogue would be reported as missing from an archive that in fact
# holds it.


def unread(**overrides) -> VideoState:
    """A video as the catalogue describes it, with no archive listing at all."""
    return video(directories=None, **overrides)


def test_the_same_work_is_found_without_the_archive():
    """The property the split rests on: which videos need something is a
    question about the videofile rows, so an operator with an API token and no
    SSH key plans the same set a worker would."""
    stale = dict(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    with_archive = [a.file_format for a in actions_of(video(**stale)) if isinstance(a, ProduceFormat)]
    without = [a.file_format for a in actions_of(unread(**stale)) if isinstance(a, ProduceFormat)]

    assert without == with_archive == [VideoFileVariantEnum.DASH]


def test_nothing_is_reported_missing_from_an_archive_nobody_read():
    result = plan(
        unread(
            files=(
                registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
                registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
                registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
            )
        ),
        DESIRED,
    )

    assert not any("registered but missing" in note for note in result.notes)


def test_nothing_is_swapped_out_on_the_strength_of_a_listing_nobody_made():
    """The swap is the worker's to decide, when it re-plans with the archive in
    front of it. A stale format still has to be rebuilt, but a plan made from
    the catalogue alone must not name a directory to trash."""
    state = unread(
        files=(
            registered(VideoFileVariantEnum.ORIGINAL, "source.mp4", file_id=1),
            registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
            registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
        )
    )

    [produce] = [a for a in actions_of(state) if isinstance(a, ProduceFormat)]

    assert produce.file_format == VideoFileVariantEnum.DASH
    assert produce.replacing is None


def test_a_legacy_source_is_still_reported_without_the_archive():
    """The rows alone say it: something is registered, and the file everything
    derives from is not among it. That count is how an operator sees how far
    the broadcast migration still has to go, so it must not need an SSH key."""
    result = plan(unread(files=broadcast_only().files), DESIRED)

    assert not result.actions
    assert any("no original is registered" in note for note in result.notes)


def test_a_video_with_no_original_is_silent_when_the_archive_was_not_read():
    """It cannot tell a video whose ladder went missing from one nobody has
    uploaded to, so it says nothing rather than guessing."""
    result = plan(unread(files=()), DESIRED)

    assert not result.actions
    assert not result.notes


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
    assert first.actions

    # What the archive and the catalogue look like once that has been carried
    # out against a source with nothing to measure: framerate written, loudness
    # exactly as it was.
    second = plan(unmeasured(), DESIRED)

    assert not second.actions


# --- the plan itself -------------------------------------------------------


def test_chores_can_be_selected():
    """Which is about what a run *reports*, not about what a worker will do:
    a worker that claims one of these re-plans it and runs everything."""
    state = video(framerate=None)

    assert plan(state, DESIRED, chores=("metadata",)).actions
    assert not plan(state, DESIRED, chores=("formats",)).actions


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


class TestThePreview:
    """When a stand-in gets built, and when it gets destroyed.

    The preview exists to cover the hours between an upload arriving and its
    ladder finishing, so every case here is really the same question: is there
    currently anything this video can be watched with?
    """

    def test_a_video_with_no_ladder_gets_one_before_the_ladder_is_built(self):
        state = video(files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4"),))

        produced = [a.file_format for a in plan(state, DESIRED).actions if isinstance(a, ProduceFormat)]

        assert DASH_PREVIEW in produced
        assert produced.index(DASH_PREVIEW) < produced.index(VideoFileVariantEnum.DASH)

    def test_the_thumbnails_still_come_first(self):
        """They are seconds, and the preview is minutes. Nothing is served by
        making the still wait behind an encode.

        Against the shipped DESIRED_FORMATS rather than this module's small
        stand-in, because that is the only thing the ordering is a property of:
        the preview is inserted immediately before `dash` in whatever order
        that tuple is iterated, so reordering it there is what would move the
        thumbnails behind the encode.
        """
        state = video(files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4"),))

        actions = plan(state, DesiredState.from_templates()).actions
        produced = [a.file_format for a in actions if isinstance(a, ProduceFormat)]

        for thumb in (
            VideoFileVariantEnum.LARGE_THUMB,
            VideoFileVariantEnum.MED_THUMB,
            VideoFileVariantEnum.SMALL_THUMB,
        ):
            assert produced.index(thumb) < produced.index(DASH_PREVIEW)

    def test_a_stale_ladder_is_rebuilt_without_one(self):
        """A rebuild already has something that plays. Replacing it with a
        preview for the length of an encode would be a downgrade for a video
        people are watching right now."""
        state = video(
            files=(
                registered(VideoFileVariantEnum.ORIGINAL, "source.mp4"),
                registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=1, file_id=2),
                registered(VideoFileVariantEnum.LARGE_THUMB, "source.jpg", revision=1, file_id=3),
            )
        )

        produced = [a.file_format for a in plan(state, DESIRED).actions if isinstance(a, ProduceFormat)]

        assert produced == [VideoFileVariantEnum.DASH]

    def test_it_is_retired_after_the_ladder_that_supersedes_it(self):
        """Last, so a ladder that fails takes the plan down before the thing
        standing in for it is destroyed."""
        state = video(files=(registered(VideoFileVariantEnum.ORIGINAL, "source.mp4"),))

        actions = plan(state, DESIRED).actions

        assert isinstance(actions[-1], RetirePreview)
        dash = [i for i, a in enumerate(actions) if isinstance(a, ProduceFormat) and a.file_format == "dash"]
        assert dash[0] < len(actions) - 1

    def test_a_leftover_preview_is_retired_without_fetching_anything(self):
        """The retry after a run that published the ladder and then failed to
        delete the preview. There is no ladder work left, so a plan that
        insisted on the original would pull down gigabytes to delete a file."""
        state = video(files=(*video().files, registered(DASH_PREVIEW, "manifest.mpd", revision=1, file_id=9)))

        actions = plan(state, DESIRED).actions

        assert [type(a) for a in actions] == [RetirePreview]
        assert not any(a.needs_source for a in actions)

    def test_a_converged_video_with_no_preview_is_left_alone(self):
        assert plan(video(), DESIRED).actions == ()

    def test_nothing_is_destroyed_while_the_ladder_is_unbuildable(self):
        """No original means no replacement is coming, so the stand-in stays."""
        state = video(
            files=(
                registered(VideoFileVariantEnum.DASH, "manifest.mpd", revision=2, file_id=2),
                registered(DASH_PREVIEW, "manifest.mpd", revision=1, file_id=9),
            )
        )

        assert not any(isinstance(a, RetirePreview) for a in plan(state, DESIRED).actions)
