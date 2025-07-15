import os
import re
import subprocess
import time

from djangoapi import get_videofiles, update_videofile


def measure_loudness(watch_dir, move_to_dir):
    """Measure loudness for all video files missing it, a few at the time.

    Process latest videos first.
    """

    maxtime = 60  # stop processing after this amount of seconds
    pagesize = 5  # Process this amount of video file per format at the time
    start = time.time()
    for fsname in ("original", "broadcast", "theora"):
        params = {
            "format__fsname": fsname,
            "integratedLufs__isnull": True,
            "page_size": pagesize,
            "order": "-video",
        }

        for data in get_videofiles(params):
            newdata = data.copy()
            filepath = data["filename"]
            loudness = get_loudness(os.path.join(move_to_dir, filepath))
            if loudness:
                newdata.update(loudness)
            if data != newdata:
                update_videofile(newdata)
        if time.time() - start > maxtime:  # Time to stop?
            break
    return


def get_loudness(filepath):
    try:
        cmd = ["bs1770gain", "--xml", "--truepeak", filepath]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        output = output.decode("utf-8")
        integrated_lufs = re.findall(r'<integrated lufs="([\d.-]+)"', output)[-1]
        truepeak_lufs = re.findall(r'<true-peak tpfs="([\d.-]+|-inf)"', output)[-1]
        data = {
            "integratedLufs": float(integrated_lufs),
        }
        if "-inf" != truepeak_lufs:
            data["truepeakLufs"] = float(truepeak_lufs)
        return data
    except (IndexError, ValueError, FileNotFoundError):
        return None
