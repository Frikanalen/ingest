import os
from pathlib import Path

import pytest

from fk_archive_utils.profile import Profile


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def profile(archive_root: Path) -> Profile:
    return Profile(name="test", root=archive_root, manager="archive-manager")


@pytest.fixture
def profile_dir(tmp_path: Path, archive_root: Path) -> Path:
    """A profiles.d holding one usable profile, named `test`."""
    directory = tmp_path / "profiles.d"
    directory.mkdir()
    (directory / "test.toml").write_text(f'root = "{archive_root}"\nmanager = "archive-manager"\n')
    return directory


@pytest.fixture(autouse=True)
def predictable_umask():
    """Pin the umask so mode assertions mean something.

    The tools set every mode explicitly for exactly this reason -- the umask
    they inherit through sudo is not theirs to choose -- and a test that ran
    under the developer's own umask would pass or fail on whose machine it was.
    """
    previous = os.umask(0o077)
    yield
    os.umask(previous)


@pytest.fixture
def make_file(archive_root: Path):
    """Put a file in the archive the way something other than these tools would.

    A fixture rather than a helper to import: the ingest repository has a
    `tests` package of its own, and a test module importing `tests.conftest`
    picks up whichever one pytest rooted itself at.
    """

    def make(relative: str, content: bytes = b"x") -> Path:
        path = archive_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    return make
