import os
import subprocess
import time
import socket
import requests
import pytest

print("conftest.py loaded")


def git_root():
    return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()


def get_free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for_tusd(port, timeout=10):
    print("wait_for_tusd")
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
    print("tusd_binary")
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
    print("tusd_server")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    port = get_free_port()

    proc = subprocess.Popen(
        [tusd_binary, "-upload-dir", str(upload_dir), "-port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        wait_for_tusd(port)
        yield {
            "url": f"http://localhost:{port}/files/",
            "upload_dir": upload_dir,
            "proc": proc,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        out, err = proc.communicate()
        if err:
            print(f"[tusd stderr] {err.decode()}")
