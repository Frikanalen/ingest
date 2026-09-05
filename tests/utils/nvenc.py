"""Whether this machine can build the DASH ladder at all.

The ladder decodes on NVDEC and encodes three of its four rungs on NVENC, so
without an NVIDIA GPU it does not merely run slowly: ffmpeg exits before the
first frame. The tests that assert on real media therefore have to skip rather
than fail, the same way they already skip when there is no ffmpeg.

Reading `-encoders` is not enough to decide it. A build advertises av1_nvenc
whenever it was compiled with the headers, driver or no driver -- and the
headers are all a build machine ever has. The only answer that means anything
is whether a device actually opens, so this opens one.
"""

import shutil
import subprocess
from functools import lru_cache

import pytest


@lru_cache
def nvenc_available() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        return (
            subprocess.run(
                # Smallest encode that still proves the whole path: a CUDA
                # device is initialized, av1_nvenc opens a session on it, and
                # a frame comes out the other side.
                "ffmpeg -hide_banner -loglevel error -f lavfi "
                "-i testsrc2=size=128x128:rate=1:duration=1 -c:v av1_nvenc -f null -",
                shell=True,
                capture_output=True,
                timeout=60,
            ).returncode
            == 0
        )
    except (subprocess.SubprocessError, OSError):
        return False


#: For the tests that build real ladder output.
requires_nvenc = pytest.mark.skipif(not nvenc_available(), reason="no NVENC-capable ffmpeg and GPU")
