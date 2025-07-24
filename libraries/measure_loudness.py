import logging
import os
import re
import subprocess
from libraries.django_api.service import DjangoApiService
from libraries.loudness_measurement import LoudnessMeasurement


async def run_missing_loudness_measurements(move_to_dir):
    """Measure loudness for all video files missing it, a few at the time.

    Process latest videos first.
    """
    django_api = DjangoApiService()
    video_files = django_api.get_original_files_without_loudness()

    logging.info("found %d files with outstanding loudness measurements", video_files.count)

    for video_file in video_files:
        if loudness := get_loudness(os.path.join(move_to_dir, video_file["filename"])):
            await django_api.set_video_loudness(video_file["video_id"], loudness)


def get_loudness(filepath) -> LoudnessMeasurement | None:
    try:
        cmd = ["bs1770gain", "--xml", "--truepeak", filepath]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        output = output.decode("utf-8")
        integrated_lufs = re.findall(r'<integrated lufs="([\d.-]+)"', output)[-1]
        truepeak_lufs = re.findall(r'<true-peak tpfs="([\d.-]+|-inf)"', output)[-1]

        return LoudnessMeasurement(
            integrated_lufs=float(integrated_lufs),
            truepeak_lufs=None if "-inf" == truepeak_lufs else float(truepeak_lufs),
        )

    except (IndexError, ValueError, FileNotFoundError):
        return None
