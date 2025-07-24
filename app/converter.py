from pathlib import Path

import yaml
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel


class ProfileMetadata(BaseModel):
    output_file_extension: str


class Converter:
    DESIRED_FORMATS = ("large_thumb",)
    env = Environment(loader=PackageLoader("ingest"), autoescape=select_autoescape())

    @staticmethod
    def _load_template_with_metadata(path: Path) -> tuple[ProfileMetadata, str]:
        content = path.read_text()
        _, yaml_block, template_body = content.split("---", 2)
        metadata = ProfileMetadata(**yaml.safe_load(yaml_block))

        return metadata, template_body.replace("\n", "")

    def render_cmd(self, profile_name: str, input_path: Path, output_path: Path, thumb_sec: int | None = None) -> str:
        tmpl = self.env.get_template(f"{profile_name}.j2")

        rendered = tmpl.render(input=str(input_path), output=str(output_path), thumb_sec=thumb_sec or 30)
        print(rendered)
        return rendered

    def convert_cmds(self, input_file_path: Path, format_name: str, metadata=None) -> tuple[str, Path]:
        output_directory = input_file_path.parent.parent / format_name
        output_file_spec = output_directory / f"{input_file_path.stem}"
        output_directory.mkdir(exist_ok=True)

        # dur = int(metadata and metadata["duration"] * 0.25 or 30)
        # cmd.extend([arg.format(thumb_sec=dur) for arg in cls.CONVERTER_PROFILES[format_name]["ffmpeg"]])

        return self.render_cmd(
            profile_name=format_name, input_path=input_file_path, output_path=output_file_spec
        ), output_file_spec
