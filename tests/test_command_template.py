from pathlib import Path

from frikanalen_django_api_client.models import FormatEnum

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
