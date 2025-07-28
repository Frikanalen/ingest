import logging
from pathlib import Path

from app.media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from app.media.ffprobe_schema import FfprobeOutput
from runner import Runner

logger = logging.getLogger(__name__)


class Converter:
    ffmpeg_command_factory: TemplatedCommandGenerator
    runner: Runner

    def __init__(self, runner: Runner):
        self.runner = runner

    async def process_format(
        self,
        input_file: Path,
        output_file: Path,
        template: TemplatedCommandGenerator,
        metadata: FfprobeOutput,
    ):
        cmd_line = template.render(
            ProfileTemplateArguments(
                input_file=input_file,
                output_file=output_file,
                seek_s=(float(metadata.format.duration) * 0.25 or 30),
            )
        )

        await self.runner.run(cmd_line)
        await self.runner.wait_for_completion()
