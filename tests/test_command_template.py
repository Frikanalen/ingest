from pathlib import Path

import pytest

from app.django_client.service import FormatEnum
from app.media.comand_template import ProfileMetadata, ProfileTemplateArguments, TemplatedCommandGenerator


def template_args(**overrides) -> ProfileTemplateArguments:
    return ProfileTemplateArguments(
        **{
            "input_file": Path("./hello"),
            "output_file": Path("./there"),
            "output_dir": Path("."),
            "scratch_dir": Path("./scratch"),
            "seek_s": 0.2,
            "has_audio": True,
            **overrides,
        }
    )


def test_large_thumb_command_looks_as_expected():
    template = TemplatedCommandGenerator(FormatEnum.LARGE_THUMB)
    command = template.render(template_args(output_file=Path("./it would be weird for this to be a file huh")))
    expected_command = 'ffmpeg -nostats -i "hello" -y -threads 8 -vf scale=720:-1 -aspect 16:9 -vframes 1 -ss 0.2 "it would be weird for this to be a file huh"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_large_thumb_is_a_single_pass_by_default():
    assert TemplatedCommandGenerator(FormatEnum.LARGE_THUMB).metadata.passes == 1


def test_h264_med_reports_progress_on_stdout():
    # Not a FormatEnum member -- this template exists but nothing wires it
    # up to a DESIRED_FORMATS entry yet.
    template = TemplatedCommandGenerator("h264_med")
    command = template.render(template_args())

    assert "-progress pipe:1" in command
    assert template.metadata.passes == 1


def test_webm_med_is_a_two_pass_template_that_reports_progress_on_each_pass():
    template = TemplatedCommandGenerator(FormatEnum.WEBM_MED)
    command = template.render(template_args())

    assert template.metadata.passes == 2
    assert command.count("-progress pipe:1") == 2


def test_webm_med_keeps_its_pass_log_out_of_the_archived_directory():
    """Whatever is left in output_dir gets archived, so the log cannot live there."""
    command = TemplatedCommandGenerator(FormatEnum.WEBM_MED).render(
        template_args(output_dir=Path("/work/webm_med"), scratch_dir=Path("/work"))
    )

    assert '-passlogfile "/work/webm_med_pass"' in command
    assert "/work/webm_med/" not in command.split("-passlogfile")[1].split()[0]


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
