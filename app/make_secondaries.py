import logging
from pathlib import Path

from frikanalen_django_api_client.models import VideoFileRequest

from app.django_api.service import DjangoApiService
from app.ffprobe_schema import FfprobeOutput
from ffmpeg.command_factory import FfmpegCommandFactory
from runner import Runner


async def convert_video_format(
    filepath: Path,
    format_name: str,
    metadata: FfprobeOutput,
    runner: Runner,
) -> Path:
    """Convert video to specified format and return target file path"""
    ffmpeg_command_factory = FfmpegCommandFactory()
    cmd_line, target_file_name = ffmpeg_command_factory.convert_cmds(filepath, format_name, metadata)
    logging.info("Producing: %s", target_file_name)
    await runner.run(cmd_line)
    await runner.wait_for_completion()

    return target_file_name


async def make_secondaries(
    video_id,
    filepath: Path,
    metadata: FfprobeOutput,
    runner: Runner,
    django_api: DjangoApiService,
):
    ffmpeg_command_factory = FfmpegCommandFactory()
    logging.info("Processing: %s", filepath)

    for format_name in ffmpeg_command_factory.DESIRED_FORMATS:
        # Generate the file
        target_file_name = await convert_video_format(filepath, format_name, metadata, runner)

        # Register it with API
        video_file = VideoFileRequest(filename=str(target_file_name), format_=format_name, video_id=video_id)
        await django_api.create_video_file(video_file=video_file)

    logging.info("Finished processing: %s", video_id)
