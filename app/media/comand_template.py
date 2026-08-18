from pathlib import Path
from typing import TypedDict

import yaml
from jinja2 import Template
from pydantic import BaseModel, model_validator

from app.media.loudness.loudness_measurement import LoudnessMeasurement
from tests.get_git_root import get_git_root


class ProfileMetadata(BaseModel):
    #: Extension of an output named after the source file, e.g. `webm`.
    output_file_extension: str | None = None
    #: A fixed output filename, for a format whose output is referenced by
    #: name from a manifest and so cannot follow the source file's stem.
    output_file_name: str | None = None
    #: How many ffmpeg invocations this template chains (two-pass encoding
    #: templates emit two `-progress` streams back to back). Used to turn
    #: ffmpeg's per-pass progress into progress across the whole command.
    passes: int = 1

    @model_validator(mode="after")
    def _names_its_output_exactly_one_way(self) -> "ProfileMetadata":
        if bool(self.output_file_extension) == bool(self.output_file_name):
            raise ValueError("a template needs either output_file_extension or output_file_name, not both or neither")
        return self

    def output_name_for(self, source_file: Path) -> str:
        """What this template's primary output is called for a given source."""
        return self.output_file_name or f"{source_file.stem}.{self.output_file_extension}"


class ProfileTemplateArguments(TypedDict):
    input_file: Path
    #: The format's primary output: the file registered with django-api, and
    #: for a multi-file format the manifest that names the rest.
    output_file: Path
    #: Everything left in here once the command succeeds is archived, so a
    #: template must not put intermediates in it -- see scratch_dir.
    output_dir: Path
    #: Working space for anything that must not reach the archive, such as
    #: two-pass log files. Discarded with the rest of the job's scratch.
    scratch_dir: Path
    seek_s: int
    #: Whether the source has an audio track at all. A template that would
    #: otherwise declare an empty audio output has to leave it out entirely:
    #: an adaptation set with no representation in it is not valid DASH.
    has_audio: bool
    #: The source's measured loudness, where we have one. None means the
    #: analysis pass found nothing to work from, and a template that would
    #: normalize has to pass the audio through at its original level
    #: instead -- a wrong gain is worse than no gain.
    loudness: LoudnessMeasurement | None


def _read_template_file(format_name: str) -> str:
    return (get_git_root() / "templates" / f"{format_name}.j2").read_text()


def _split_content_and_metadata(content: str) -> tuple[str, str]:
    """Splits the content into metadata and template body."""
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Template content must contain exactly one YAML block followed by the template body.")
    return parts[1], parts[2]


class TemplatedCommandGenerator:
    def __init__(self, format_name: str):
        metadata, template = _split_content_and_metadata(_read_template_file(format_name))
        self.template_metadata = ProfileMetadata(**yaml.safe_load(metadata))
        self.template = Template(template.replace("\n", ""))

    @property
    def metadata(self) -> ProfileMetadata:
        return self.template_metadata

    def render(self, args: ProfileTemplateArguments) -> str:
        return self.template.render(**args)
