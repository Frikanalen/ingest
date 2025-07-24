import json
import logging
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime

from frikanalen_django_api_client.models import VideoFile, VideoFileRequest

from .converter import Converter
from video_formats import VF_FORMATS
from .fk_exceptions import AppError
from .loudness.get_loudness import get_loudness
from runner import Runner
from libraries.django_api.service import DjangoApiService


async def update_existing_file(video_id: str, archive_path: Path):
    logging.info("Trying to update existing file id: %d in folder %s", video_id, archive_path)
    if not (archive_path / video_id).is_dir():
        raise AppError("No folder %s/ in %s" % (video_id, archive_path))

    # FIXME: Broken code
    for folder in ["original", "broadcast"]:
        path = archive_path / video_id / folder

        if not path.is_dir():
            raise AppError("Found no file for %s" % (video_id,))

        [file_name] = [f for f in path.iterdir()]

    filepath = path / file_name
    metadata = get_metadata(filepath)
    django_api = DjangoApiService()
    await django_api.set_video_duration(video_id, metadata["duration"])
    await django_api.set_video_uploaded_time(video_id, datetime.now())
    await generate_videos(video_id, filepath)
    await django_api.set_video_proper_import(video_id)


def get_metadata(filepath):
    md = ffprobe_file(filepath)
    md["mlt_duration"] = get_mlt_duration(filepath)
    md["duration"] = md["mlt_duration"] or md["format"]["duration"]
    md["pretty_duration"] = pretty_duration(md["duration"])
    return md


def get_mlt_duration(filepath):
    cmd = ["melt", "-consumer", "xml", filepath]
    output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    output = output.decode("utf-8")
    m = re.search(r' name="length">(\d+)</', output)
    if not m:
        return
    frames = int(m.group(1))
    m = re.search(r'\.frame_rate">([\d.]+)</', output)
    if not m:
        return
    fps = float(m.group(1))
    return frames / fps


def pretty_duration(duration):
    min, sec = divmod(duration, 60)
    hours, _ = divmod(min, 60)
    return "{:d}:{:02d}:{:02f}".format(int(hours), int(min), sec)


def ffprobe_file(filepath):
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        filepath,
    ]
    output = subprocess.check_output(cmd)
    return json.loads(output.decode("utf-8"))


async def register_video_files(video_id: str, video_path: Path, django_api=DjangoApiService()):
    logging.info("Registering files for video %s in folder %s", video_id, video_path)

    files = await django_api.get_files_for_video(video_id)
    existing_file_names = [f["filename"].strip() for f in files]

    logging.info("Found %d existing files for video %s", len(files), video_id)

    for format_path in video_path.iterdir():
        logging.info("Registering files in folder %s", format_path)
        for file_name in (video_path / format_path).iterdir():
            logging.info("Registering file %s", file_name)
            filename = os.path.join(video_id, format_path, file_name)
            if filename in existing_file_names:
                logging.error("Not writing file because a file with the same name already exists: %s", filename)
                continue

            loudness = get_loudness(os.path.join(video_path, "..", filename))
            await django_api.create_video_file(
                video_file=VideoFile.from_dict(
                    {
                        "filename": filename,
                        "format_": VF_FORMATS[format_path],
                        "loudness": loudness,
                        "video": video_id,
                    }
                ),
            )


async def generate_videos(
    video_id,
    filepath: Path,
    runner=None,
    converter=None,
    django_api=None,
    metadata=None,
    register=None,
):
    if runner is None:
        runner = Runner()
    if converter is None:
        converter = Converter()
    if django_api is None:
        django_api = DjangoApiService()
    if register is None:
        register = register_video_files

    logging.info("Processing: %s", filepath)
    base_path = filepath.parent.parent
    for format_name in converter.DESIRED_FORMATS:
        cmd_line, target_file_name = converter.convert_cmds(filepath, format_name, metadata)
        logging.info("Running: %s", cmd_line)
        runner.run(cmd_line)
        await django_api.create_video_file(
            video_file=VideoFileRequest(filename=str(target_file_name), format_=format_name, video_id=video_id)
        )

        await register(video_id, base_path)
