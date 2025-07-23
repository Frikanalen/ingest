import json
import logging
import os
import re
import subprocess
from datetime import datetime

from .converter import Converter
from .djangoapi import update_video, get_videofiles, create_videofile
from video_formats import VF_FORMATS
from .fk_exceptions import AppError
from .measure_loudness import get_loudness
from runner import Runner


def update_existing_file(id, to_dir, force):
    logging.info("Trying to update existing file id: %d in folder %s", id, to_dir)
    str_id = str(id)
    if not os.path.isdir(os.path.join(to_dir, str_id)):
        raise AppError("No folder %d/ in %s" % (id, to_dir))
    fn = None
    path = None
    for folder in ["original", "broadcast"]:
        path = os.path.join(to_dir, str_id, folder)
        if os.path.isdir(path):
            fn = os.listdir(path)[0]
            break
    else:
        raise AppError("Found no file for %d, last checked in %s" % (id, path))
    filepath = os.path.join(path, fn)
    metadata = get_metadata(filepath)
    update_video(
        id,
        {
            "duration": metadata["pretty_duration"],
            "uploadedTime": datetime.utcnow().isoformat(),
        },
    )
    generate_videos(id, filepath, metadata, reprocess=True)
    update_video(id, {"properImport": True})


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


def register_videofiles(id, folder, videofiles=None):
    files = get_videofiles({"video_id": id})
    videofiles = (videofiles or set()).union({f["filename"].strip() for f in files})
    for file_folder in os.listdir(folder):
        for fn in os.listdir(os.path.join(folder, file_folder)):
            filepath = os.path.join(str(id), file_folder, fn)
            if filepath in videofiles:
                continue
            data = {
                "filename": filepath,
                "format": VF_FORMATS[file_folder],
            }
            loudness = get_loudness(os.path.join(folder, "..", filepath))
            # Handle images, which do not have loudness
            if loudness:
                data.update(loudness)
            create_videofile(id, data)
            videofiles.add(filepath)
    return videofiles


def generate_videos(
    id,
    filepath,
    metadata=None,
    runner_run=Runner.run,
    converter=Converter,
    register=register_videofiles,
    reprocess=False,
):
    logging.info("Processing: %s", filepath)
    base_path = os.path.dirname(os.path.dirname(filepath))
    formats = converter.get_formats(filepath)
    videofiles = set()
    for t in formats:
        cmds, new_fn = converter.convert_cmds(filepath, t, metadata)
        runner_run(cmds, filepath=new_fn, reprocess=reprocess)
        videofiles = register(id, base_path, videofiles=videofiles)
