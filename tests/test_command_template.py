import re
from pathlib import Path

import pytest

from app.django_client.service import FormatEnum
from app.media.comand_template import ProfileMetadata, ProfileTemplateArguments, TemplatedCommandGenerator
from app.media.loudness.loudness_measurement import LoudnessMeasurement

MEASURED = LoudnessMeasurement(
    integrated_lufs=-27.85,
    truepeak_lufs=-9.61,
    loudness_range=5.2,
    threshold_lufs=-38.2,
    target_offset=0.15,
)


def template_args(**overrides) -> ProfileTemplateArguments:
    return ProfileTemplateArguments(
        **{
            "input_file": Path("./hello"),
            "output_file": Path("./there"),
            "output_dir": Path("."),
            "scratch_dir": Path("./scratch"),
            "seek_s": 0.2,
            "has_audio": True,
            "loudness": None,
            **overrides,
        }
    )


def test_large_thumb_command_looks_as_expected():
    template = TemplatedCommandGenerator(FormatEnum.LARGE_THUMB)
    command = template.render(template_args(output_file=Path("./it would be weird for this to be a file huh")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=720:-1 -aspect 16:9 -frames:v 1 "it would be weird for this to be a file huh"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_thumbnails_seek_before_they_decode():
    """`-ss` after `-i` is an output option: ffmpeg decodes and discards every
    frame up to the seek point, so three thumbnails a quarter of the way in
    cost most of a decode pass. Before `-i` it is an input seek instead."""
    for thumb in (FormatEnum.LARGE_THUMB, FormatEnum.MED_THUMB, FormatEnum.SMALL_THUMB):
        command = TemplatedCommandGenerator(thumb).render(template_args())

        assert command.index("-ss ") < command.index("-i "), command


def test_large_thumb_is_a_single_pass_by_default():
    assert TemplatedCommandGenerator(FormatEnum.LARGE_THUMB).metadata.passes == 1


def test_med_thumb_is_narrower_than_large_thumb():
    template = TemplatedCommandGenerator(FormatEnum.MED_THUMB)
    command = template.render(template_args(output_file=Path("./out.jpg")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=320:-1 -aspect 16:9 -frames:v 1 "out.jpg"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_small_thumb_is_narrower_than_med_thumb():
    template = TemplatedCommandGenerator(FormatEnum.SMALL_THUMB)
    command = template.render(template_args(output_file=Path("./out.jpg")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=120:-1 -aspect 16:9 -frames:v 1 "out.jpg"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_h264_med_reports_progress_on_stdout():
    # Not a FormatEnum member -- this template exists but nothing wires it
    # up to a DESIRED_FORMATS entry yet.
    template = TemplatedCommandGenerator("h264_med")
    command = template.render(template_args())

    assert "-progress pipe:1" in command
    assert template.metadata.passes == 1


def test_dash_names_its_output_rather_than_following_the_source():
    """The manifest names its media, so those names must not carry a source stem
    that would need percent-encoding to survive as a URL."""
    metadata = TemplatedCommandGenerator(FormatEnum.DASH).metadata

    assert metadata.output_name_for(Path("/uploads/Some Show, Episode 3.mov")) == "manifest.mpd"


def test_dash_is_a_single_ffmpeg_invocation():
    """One decode feeding every rendition, so ffmpeg's progress needs no scaling."""
    template = TemplatedCommandGenerator(FormatEnum.DASH)
    command = template.render(template_args())

    assert template.metadata.passes == 1
    assert command.count("ffmpeg") == 1
    assert command.count("-progress pipe:1") == 1


def test_dash_encodes_three_renditions_and_never_upscales():
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args())

    assert command.count("libvpx-vp9") == 3
    # min(rung, ih) rather than a bare height: a 576i upload must not be
    # blown up to 1080p and charged three times for the privilege.
    for rung in (1080, 720, 360):
        assert f"scale=-2:min({rung}" in command


def test_dash_keyframes_are_pinned_to_wall_clock_not_frame_count():
    """A GOP length in frames silently misaligns renditions the moment
    something other than 25fps is uploaded."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args())

    assert "-force_key_frames" in command
    assert "-g " not in command


def test_dash_forces_keyframes_no_more_often_than_it_starts_segments():
    """Renditions can only be switched at a segment boundary, so a keyframe
    inside a segment buys nothing but seek granularity -- and keyframes are
    both the most expensive frames to encode and the worst to compress."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args())

    seg_duration = re.search(r"-seg_duration (\d+)", command)
    keyframe_interval = re.search(r"expr:gte\(t,n_forced\*(\d+)\)", command)

    assert seg_duration and keyframe_interval, command
    assert int(keyframe_interval.group(1)) == int(seg_duration.group(1))


def test_dash_normalizes_a_measured_source_to_the_web_target():
    """-16 LUFS is what the rest of a browser tab sounds like. Playout works
    to -23 from the figure stored against the original instead, which is why
    the measurement describes the upload rather than this output."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args(loudness=MEASURED))

    assert "loudnorm=I=-16:TP=-1" in command
    for measured in ("measured_I=-27.85", "measured_TP=-9.61", "measured_LRA=5.2", "measured_thresh=-38.2"):
        assert measured in command, command


def test_dash_normalizes_in_one_linear_pass_rather_than_riding_the_gain():
    """Without the measurements loudnorm works dynamically, which pumps
    quiet passages up and audibly breathes on speech."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args(loudness=MEASURED))

    assert "linear=true" in command
    assert "offset=0.15" in command


def test_dash_resamples_after_normalizing():
    """loudnorm outputs 192kHz, which libopus does not accept -- without a
    resampler the encode fails outright rather than sounding wrong."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args(loudness=MEASURED))

    assert command.index("loudnorm") < command.index("-ar 48000") < command.index("libopus")


def test_dash_leaves_the_level_alone_when_nothing_was_measured():
    """A wrong gain is worse than no gain."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args(loudness=None))

    assert "loudnorm" not in command
    assert "-map 0:a:0 -c:a libopus" in command


def test_dash_does_not_normalize_a_source_with_no_measurable_peak():
    """loudnorm has no syntax for an unknown true peak, and rendering the
    null into the filter would produce a command ffmpeg cannot parse."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(
        template_args(loudness=MEASURED.model_copy(update={"truepeak_lufs": None}))
    )

    assert "loudnorm" not in command
    assert "None" not in command


def test_dash_leaves_out_the_audio_adaptation_set_when_there_is_no_audio():
    """An adaptation set with no representation in it is not valid DASH."""
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args(has_audio=False))

    assert "libopus" not in command
    assert "streams=a" not in command
    assert '-adaptation_sets "id=0,streams=v"' in command


def test_dash_includes_the_audio_adaptation_set_when_there_is_audio():
    command = TemplatedCommandGenerator(FormatEnum.DASH).render(template_args(has_audio=True))

    assert "-map 0:a:0 -c:a libopus" in command
    assert '-adaptation_sets "id=0,streams=v id=1,streams=a"' in command


def test_a_template_must_say_how_its_output_is_named():
    with pytest.raises(ValueError):
        ProfileMetadata()

    with pytest.raises(ValueError):
        ProfileMetadata(output_file_extension="webm", output_file_name="manifest.mpd")
