import logging
from pathlib import Path

from frikanalen_django_api_client.models import FormatEnum, VideoFileRequest

from app.django_api.service import DjangoApiService
from app.ffprobe_schema import FfprobeOutput
from ffmpeg.command_factory import FfmpegCommandFactory
from runner import Runner


class Converter:
    ffmpeg_command_factory = FfmpegCommandFactory()
    django_api: DjangoApiService
    runner: Runner

    def __init__(self, django_api: DjangoApiService, runner: Runner):
        self.runner = runner
        self.django_api = django_api

    async def process_format(
        self,
        input_file_path: Path,
        output_format_name: FormatEnum,
        metadata: FfprobeOutput,
        video_id: int,
    ):
        logging.info("Producing: %s", output_format_name)
        ffmpeg_command_factory = FfmpegCommandFactory()

        cmd_line, target_file_name = ffmpeg_command_factory.build_ffmpeg_command(
            input_file_path, output_format_name, metadata
        )
        await self.runner.run(cmd_line)
        await self.runner.wait_for_completion()

        # Register it with API
        req = VideoFileRequest(filename=str(target_file_name), format_=output_format_name, video_id=video_id)
        await self.django_api.create_video_file(video_file=req)
