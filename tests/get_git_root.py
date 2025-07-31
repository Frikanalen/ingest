import subprocess
from pathlib import Path


def get_git_root():
    try:
        return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    except Exception:
        return Path("/app")
