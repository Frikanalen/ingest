import subprocess
from pathlib import Path


def get_git_root():
    return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
