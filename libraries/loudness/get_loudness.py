import re
import subprocess

from libraries.loudness.loudness_measurement import LoudnessMeasurement


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
