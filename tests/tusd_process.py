from dataclasses import field
import logging
import re
import subprocess
import threading
import time
from logging import Logger
from pathlib import Path
from typing import TextIO, List

import requests

from tests.tusd_config import TusdConfig, build_tusd_command
from tests.tusd_log_parser import parse_tusd_line


class TusdProcess:
    upload_dir: Path
    config: TusdConfig
    proc: subprocess.Popen | None
    stdout_thread: threading.Thread | None
    stderr_thread: threading.Thread | None
    hooks_url: str = None
    _extra_args: List[str] = field(default_factory=list)

    def __init__(self, config: TusdConfig = None):
        self.config = config

    @property
    def url(self):
        return f"http://localhost:{self.config.port}/files/"

    def start(self):
        """Start the tusd process"""
        self.proc = subprocess.Popen(
            build_tusd_command(config=self.config),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
        )

        self.stdout_thread = threading.Thread(
            target=_stream_output,
            args=(self.proc.stdout, logging.getLogger("tusd.stdout")),
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=_stream_output,
            args=(self.proc.stderr, logging.getLogger("tusd.stderr")),
            daemon=True,
        )

        self.stdout_thread.start()
        self.stderr_thread.start()

        wait_for_tusd(self.config.port)
        return self

    def stop(self):
        """Stop the tusd process"""
        if self.proc:
            self.proc.terminate()
            self.stderr_thread.join()
            self.stdout_thread.join()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            # Let threads finish if there's anything buffered
            time.sleep(0.5)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


_ts_prefix = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{6}\s+")


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


def _stream_output(pipe: TextIO, logger: Logger):
    for line in pipe:
        parsed = parse_tusd_line(line)
        if parsed.entry_type == "structured":
            logger.info(parsed.fields, extra=parsed.fields)
        else:
            logger.info(parsed.message)
