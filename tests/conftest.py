import logging
import os
from typing import TextIO

import pytest
import requests
import socket
import subprocess
import threading
import time
import re

from tusd_log_parser import parse_tusd_line

_ts_prefix = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{6}\s+")


def git_root():
    return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()


def get_free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for_tusd(port, timeout=10):
    url = f"http://localhost:{port}/files/"
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.options(url)
            if r.status_code in (200, 204):
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"tusd did not start in time on port {port}")


@pytest.fixture(scope="session")
def tusd_binary():
    root = git_root()
    bin_path = os.path.join(root, "bin")
    tusd_path = os.path.join(bin_path, "tusd")

    if not os.path.exists(tusd_path):
        os.makedirs(bin_path, exist_ok=True)
        subprocess.run(
            ["go", "install", "github.com/tus/tusd/v2/cmd/tusd@latest"],
            check=True,
            env={**os.environ, "GOBIN": bin_path},
        )

    return tusd_path


def _stream_output(pipe: TextIO, name: str):
    logger = logging.getLogger(f"tusd.{name}")

    for line in pipe:
        parsed = parse_tusd_line(line)
        if parsed.entry_type == "structured":
            logger.info(parsed.fields, extra=parsed.fields)
        else:
            logger.info(parsed.message)


@pytest.fixture
def tusd_server(tmp_path, tusd_binary):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    port = get_free_port()

    proc = subprocess.Popen(
        [tusd_binary, "-upload-dir", str(upload_dir), "-port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        universal_newlines=True,
    )

    # Start background threads to stream stdout/stderr
    stdout_thread = threading.Thread(
        target=_stream_output, args=(proc.stdout, "stdout"), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_stream_output, args=(proc.stderr, "stderr"), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        wait_for_tusd(port)
        yield {
            "url": f"http://localhost:{port}/files/",
            "upload_dir": upload_dir,
            "proc": proc,
        }
    finally:
        proc.terminate()
        stderr_thread.join()
        stdout_thread.join()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        # Let threads finish if there's anything buffered
        time.sleep(0.5)
