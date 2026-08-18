from pathlib import Path

from app.django_client.service import FormatEnum
from app.media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator


def test_large_thumb_command_looks_as_expected():
    template = TemplatedCommandGenerator(FormatEnum.LARGE_THUMB)
    template_args = ProfileTemplateArguments(
        input_file=(Path("./hello")),
        output_file=(Path("./it would be weird for this to be a file huh")),
        seek_s=0.2,
    )

    command = template.render(template_args)
    expected_command = 'ffmpeg -nostats -i "hello" -y -threads 8 -vf scale=720:-1 -aspect 16:9 -vframes 1 -ss 0.2 "it would be weird for this to be a file huh"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_large_thumb_is_a_single_pass_by_default():
    assert TemplatedCommandGenerator(FormatEnum.LARGE_THUMB).metadata.passes == 1


def test_h264_med_reports_progress_on_stdout():
    # Not a FormatEnum member -- this template exists but nothing wires it
    # up to a DESIRED_FORMATS entry yet.
    template = TemplatedCommandGenerator("h264_med")
    template_args = ProfileTemplateArguments(
        input_file=(Path("./hello")),
        output_file=(Path("./there")),
        seek_s=0.2,
    )

    command = template.render(template_args)

    assert "-progress pipe:1" in command
    assert template.metadata.passes == 1


def test_webm_med_is_a_two_pass_template_that_reports_progress_on_each_pass():
    template = TemplatedCommandGenerator(FormatEnum.WEBM_MED)
    template_args = ProfileTemplateArguments(
        input_file=(Path("./hello")),
        output_file=(Path("./there")),
        seek_s=0.2,
    )

    command = template.render(template_args)

    assert template.metadata.passes == 2
    assert command.count("-progress pipe:1") == 2
