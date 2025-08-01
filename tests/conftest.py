import logging
import multiprocessing
import os
import shlex
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from tests.get_git_root import get_git_root
from tests.run_test_server import run_server
from tests.utils.get_free_port import get_free_port
from tests.utils.tusd_command import TusdConfig, TusdHttpHookConfig
from tests.utils.tusd_process import TusdProcess

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def tusd_binary() -> str:
    git_root = get_git_root()
    tusd_path = git_root / "bin" / "tusd"

    if not tusd_path.exists():
        logger.warning("tusd binary not found, installing it")
        try:
            subprocess.run(
                ["go", "install", "github.com/tus/tusd/v2/cmd/tusd@latest"],
                check=True,
                env={**os.environ, "GOBIN": str(git_root / "bin")},
            )
        except FileNotFoundError:
            logging.error("go not found, please install it and try again")
            raise OSError('"go" not found in path, do you need to install golang?') from None

    return str(tusd_path)


@pytest.fixture
def tusd_server(tmp_path, tusd_binary):
    config = TusdConfig(binary_path=tusd_binary, upload_dir=tmp_path)

    with TusdProcess(config) as process:
        yield TusdFixture(
            url=process.url,
            upload_dir=tmp_path,
            proc=process.proc,
        )


@pytest.fixture
def tusd_server_with_hooks(tmp_path, tusd_binary, start_fastapi_server):
    config = TusdConfig(
        binary_path=tusd_binary, upload_dir=tmp_path, hook_config=TusdHttpHookConfig(url=start_fastapi_server.url)
    )

    with TusdProcess(config) as process:
        yield TusdFixtureWithHooks(
            url=process.url,
            upload_dir=tmp_path,
            proc=process.proc,
            start_fastapi_server=start_fastapi_server,
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass(frozen=True)
class MockHookServerFixture:
    url: str
    port: int


@dataclass(frozen=True)
class TusdFixture:
    url: str
    upload_dir: Path
    proc: subprocess.Popen


@dataclass(frozen=True)
class TusdFixtureWithHooks(TusdFixture):
    start_fastapi_server: MockHookServerFixture


@contextmanager
def suppress_urllib3_logging():
    """
    We expect to see a lot of urllib3 timeouts in the alive check, so let's suppress it.
    """
    url_lib_logger = logging.getLogger("urllib3.connectionpool")
    old_level = url_lib_logger.level
    url_lib_logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        url_lib_logger.setLevel(old_level)


def _wait_for_server(url: str, backoff_factor: float = 0.1, total: int = 15):
    session = requests.Session()

    session.mount(
        "http://",
        HTTPAdapter(
            max_retries=(Retry(status_forcelist=[500, 502, 503, 504], backoff_factor=backoff_factor, total=total))
        ),
    )
    with suppress_urllib3_logging():
        response = session.get(f"{url}/isAlive", timeout=3)
        response.raise_for_status()


@pytest.fixture(scope="session")
def start_fastapi_server() -> Generator[MockHookServerFixture, None, None]:
    HOST = "localhost"
    PORT = get_free_port()
    URL = f"http://{HOST}:{PORT}"

    proc = multiprocessing.Process(
        target=run_server,
        args=(
            HOST,
            PORT,
        ),
        daemon=True,
    )
    proc.start()
    try:
        _wait_for_server(url=URL)
    except requests.RequestException as e:
        proc.terminate()
        proc.join()
        raise RuntimeError("FastAPI server failed to start and respond to health check") from e
    yield MockHookServerFixture(port=PORT, url=URL)
    proc.terminate()
    proc.join()


@pytest.fixture(scope="session")
def color_bars_video() -> Generator[Path, None, None]:
    """
    Generate a 1-second color bars video using ffmpeg, once for the whole test session.
    Returns the path to the generated video file.
    """
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video_path = tmp.name

    try:
        cmd = shlex.split(
            "ffmpeg -f lavfi -i smptebars=size=1280x720:rate=25 "
            "-t 1 -y -nostats -hide_banner -loglevel error "
            "-c:v libx264 -pix_fmt yuv420p"
        )

        subprocess.run([*cmd, video_path], check=True)

        yield Path(video_path)
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)
