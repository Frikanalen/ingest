"""Archiving over SSH is optional, so ingest still runs without credentials.

Handy on a laptop, dangerous anywhere that means to archive over SSH: writing
to a local directory instead would lose files. FK_ARCHIVE_REQUIRED is what
turns the fallback back off.
"""

from pathlib import Path

import pytest

from app.archive_store import ArchiveError, LocalArchiveStore, SshArchiveStore, create_archive_store
from app.util.settings import SshArchiveSettings


@pytest.fixture
def fallback_dir(tmp_path):
    fallback = tmp_path / "archive"
    fallback.mkdir()
    return fallback


@pytest.fixture
def private_key_file(tmp_path):
    key = tmp_path / "id_ed25519"
    key.touch()
    return key


@pytest.fixture
def known_hosts_file(tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()
    return known_hosts


def test_falls_back_to_local_without_credentials(fallback_dir):
    store = create_archive_store(SshArchiveSettings(host="file01", fallback_dir=fallback_dir))

    assert isinstance(store, LocalArchiveStore)
    assert store.root == fallback_dir


def test_the_fallback_is_warned_about(fallback_dir, caplog):
    with caplog.at_level("WARNING"):
        create_archive_store(SshArchiveSettings(host="file01", fallback_dir=fallback_dir))

    assert "file01" in caplog.text
    assert str(fallback_dir) in caplog.text


def test_falls_back_when_the_key_is_missing(fallback_dir, tmp_path):
    store = create_archive_store(
        SshArchiveSettings(
            host="file01",
            private_key_file=tmp_path / "nonexistent",
            fallback_dir=fallback_dir,
        )
    )

    assert isinstance(store, LocalArchiveStore)


def test_falls_back_when_known_hosts_is_missing(fallback_dir, private_key_file, tmp_path):
    """A key without a known_hosts file is not enough: we never skip host verification."""
    store = create_archive_store(
        SshArchiveSettings(
            host="file01",
            private_key_file=private_key_file,
            known_hosts_file=tmp_path / "nonexistent",
            fallback_dir=fallback_dir,
        )
    )

    assert isinstance(store, LocalArchiveStore)


def test_required_refuses_to_fall_back(fallback_dir):
    with pytest.raises(ArchiveError):
        create_archive_store(SshArchiveSettings(host="file01", fallback_dir=fallback_dir, required=True))


def test_required_reports_what_was_missing(fallback_dir, private_key_file):
    with pytest.raises(ArchiveError, match="known_hosts"):
        create_archive_store(
            SshArchiveSettings(
                host="file01",
                private_key_file=private_key_file,
                fallback_dir=fallback_dir,
                required=True,
            )
        )


def test_a_missing_fallback_directory_is_still_an_error():
    """Falling back is only useful if the fallback actually exists."""
    with pytest.raises(ArchiveError):
        create_archive_store(SshArchiveSettings(host="file01", fallback_dir=Path("/nonexistent/archive")))


def test_complete_credentials_still_choose_ssh(fallback_dir, private_key_file, known_hosts_file):
    store = create_archive_store(
        SshArchiveSettings(
            host="file01",
            private_key_file=private_key_file,
            known_hosts_file=known_hosts_file,
            fallback_dir=fallback_dir,
        )
    )

    assert isinstance(store, SshArchiveStore)
