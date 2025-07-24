from pathlib import Path
from typing import TypedDict

import yaml
from jinja2 import Template
from pydantic import BaseModel

from app.ffprobe_schema import FfprobeOutput
from tests.conftest import get_git_root


class ProfileMetadata(BaseModel):
    output_file_extension: str


class ProfileTemplateArguments(TypedDict):
    input: Path
    output: Path
    thumbs_sec: int


class FfmpegCommandFactory:
    DESIRED_FORMATS = ("large_thumb",)

    @staticmethod
    def _load_template_with_metadata(path: Path) -> tuple[ProfileMetadata, Template]:
        content = path.read_text()
        _, yaml_block, template_body = content.split("---", 2)
        metadata = ProfileMetadata(**yaml.safe_load(yaml_block))

        return metadata, Template(template_body.replace("\n", ""))

    def convert_cmds(self, input_file_path: Path, format_name: str, metadata: FfprobeOutput) -> tuple[str, Path]:
        template_path = get_git_root() / "templates" / f"{format_name}.j2"
        template_metadata, tmpl = self._load_template_with_metadata(template_path)

        output_directory = input_file_path.parent / format_name
        output_directory.mkdir(exist_ok=True)
        output_file_spec = output_directory / f"{input_file_path.stem}.{template_metadata.output_file_extension}"
        thumbs_sec = float(metadata.format.duration) * 0.25 or 30

        cmd = tmpl.render(
            **ProfileTemplateArguments(input=input_file_path, output=output_file_spec, thumbs_sec=thumbs_sec)
        )
        print(cmd)
        return cmd, output_file_spec
