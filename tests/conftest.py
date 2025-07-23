import dataclasses
import os
import queue
from pathlib import Path
from queue import Queue
from typing import Callable

import pytest
import subprocess

from werkzeug import Request, Response

from tests.get_free_port import get_free_port
from tests.mock_hook_server import MockHookServer
from tests.tusd_process import TusdProcess
from tests.tusd_config import TusdHttpHookConfig, TusdConfig


def git_root():
    return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()


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


@pytest.fixture
def tusd_server(tmp_path, tusd_binary):
    """
    Creates a pytest fixture that sets up and tears down a tusd server for
    testing purposes. The server is configured using a temporary directory
    for uploads and a specified binary path for the server executable.

    This fixture ensures that the tusd server is properly initialized
    and cleaned up after each test, providing an isolated test environment.

    Parameters:
        tmp_path (Path): A temporary directory provided by pytest for use during testing.
        tusd_binary (Path): The file path to the tusd server binary.

    Yields:
        dict: A dictionary containing the following keys:
            - url (str): The URL of the running tusd server.
            - upload_dir (Path): The temporary directory used for uploads.
            - proc (subprocess.Popen): The process object for the running tusd server.
    """
    config = TusdConfig(binary_path=tusd_binary, upload_dir=tmp_path)

    with TusdProcess(config) as process:
        yield {
            "url": process.url,
            "upload_dir": tmp_path,
            "proc": process.proc,
        }


@pytest.fixture
def tusd_server_with_hooks(tmp_path, tusd_binary, mock_hook_server):
    config = TusdConfig(
        binary_path=tusd_binary, upload_dir=tmp_path, hook_config=TusdHttpHookConfig(url=mock_hook_server.url)
    )

    with TusdProcess(config) as process:
        yield TusdFixture(
            url=process.url,
            upload_dir=tmp_path,
            proc=process.proc,
            mock_hook_server=mock_hook_server,
        )


@dataclasses.dataclass(frozen=True)
class MockHookServerFixture:
    url: str
    port: int
    configure_response: Callable[[Request], Response]
    clear_configuration: Callable[[], None]
    recorded_requests: Queue[Request] = dataclasses.field(default_factory=queue.Queue)


@dataclasses.dataclass(frozen=True)
class TusdFixture:
    url: str
    upload_dir: Path
    proc: subprocess.Popen
    mock_hook_server: MockHookServerFixture


@pytest.fixture
def mock_hook_server():
    server = MockHookServer(port=get_free_port())
    server.start()

    try:
        yield MockHookServerFixture(
            url=f"http://localhost:{server.port}",
            port=server.port,
            recorded_requests=server.recorded_requests,
            configure_response=lambda x: server.configure_response("/", x),
            clear_configuration=server.clear_configuration,
        )
    finally:
        server.stop()
