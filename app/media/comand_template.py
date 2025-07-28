from pathlib import Path
from typing import TypedDict

import yaml
from jinja2 import Template
from pydantic import BaseModel

from tests.get_git_root import get_git_root


class ProfileMetadata(BaseModel):
    output_file_extension: str


class ProfileTemplateArguments(TypedDict):
    input_file: Path
    output_file: Path
    seek_s: int


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
