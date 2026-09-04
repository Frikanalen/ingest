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
def mount(tmp_path):
    """The archive, mounted read-only. Reads come off this rather than the SSH
    connection, so an unmounted volume is one of the ways the archive is
    unusable."""
    mounted = tmp_path / "mnt"
    mounted.mkdir()
    return mounted


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


def test_falls_back_to_local_without_credentials(mount, fallback_dir):
    store = create_archive_store(SshArchiveSettings(host="file01", dir=mount, fallback_dir=fallback_dir))

    assert isinstance(store, LocalArchiveStore)
    assert store.root == fallback_dir


def test_the_fallback_is_warned_about(mount, fallback_dir, caplog):
    with caplog.at_level("WARNING"):
        create_archive_store(SshArchiveSettings(host="file01", dir=mount, fallback_dir=fallback_dir))

    assert "file01" in caplog.text
    assert str(fallback_dir) in caplog.text


def test_falls_back_when_the_key_is_missing(mount, fallback_dir, tmp_path):
    store = create_archive_store(
        SshArchiveSettings(
            host="file01",
            dir=mount,
            private_key_file=tmp_path / "nonexistent",
            fallback_dir=fallback_dir,
        )
    )

    assert isinstance(store, LocalArchiveStore)


def test_falls_back_when_known_hosts_is_missing(mount, fallback_dir, private_key_file, tmp_path):
    """A key without a known_hosts file is not enough: we never skip host verification."""
    store = create_archive_store(
        SshArchiveSettings(
            host="file01",
            dir=mount,
            private_key_file=private_key_file,
            known_hosts_file=tmp_path / "nonexistent",
            fallback_dir=fallback_dir,
        )
    )

    assert isinstance(store, LocalArchiveStore)


def test_required_refuses_to_fall_back(mount, fallback_dir):
    with pytest.raises(ArchiveError):
        create_archive_store(SshArchiveSettings(host="file01", dir=mount, fallback_dir=fallback_dir, required=True))


def test_required_reports_what_was_missing(mount, fallback_dir, private_key_file):
    with pytest.raises(ArchiveError, match="known_hosts"):
        create_archive_store(
            SshArchiveSettings(
                host="file01",
                dir=mount,
                private_key_file=private_key_file,
                fallback_dir=fallback_dir,
                required=True,
            )
        )


def test_a_missing_fallback_directory_is_still_an_error(mount):
    """Falling back is only useful if the fallback actually exists."""
    with pytest.raises(ArchiveError):
        create_archive_store(SshArchiveSettings(host="file01", dir=mount, fallback_dir=Path("/nonexistent/archive")))


def test_complete_credentials_still_choose_ssh(mount, fallback_dir, private_key_file, known_hosts_file):
    store = create_archive_store(
        SshArchiveSettings(
            host="file01",
            dir=mount,
            private_key_file=private_key_file,
            known_hosts_file=known_hosts_file,
            fallback_dir=fallback_dir,
        )
    )

    assert isinstance(store, SshArchiveStore)


def test_an_unmounted_archive_is_unusable(fallback_dir, private_key_file, known_hosts_file, tmp_path):
    """A volume that failed to mount reads as an archive holding nothing at all.

    Every video would then observe as having no media, and a worker would set
    about rebuilding the lot. Caught where the credentials are, so it is a pod
    that will not start rather than a night of that.
    """
    with pytest.raises(ArchiveError, match="not mounted"):
        create_archive_store(
            SshArchiveSettings(
                host="file01",
                dir=tmp_path / "never-mounted",
                private_key_file=private_key_file,
                known_hosts_file=known_hosts_file,
                fallback_dir=fallback_dir,
                required=True,
            )
        )


def test_a_writable_archive_mount_is_warned_about(mount, private_key_file, known_hosts_file, caplog):
    """The read-only mount is what makes the rest of the arrangement worth having.

    Everything in archive-utils/ exists to take write access away from this
    process; a mount the kernel would let it write to hands that access back.
    Said rather than refused, because the flag is the mount's report of itself
    and not every driver fills it in -- an archive nobody can reach is worse
    than one whose posture we could not confirm.
    """
    with caplog.at_level("WARNING"):
        create_archive_store(
            SshArchiveSettings(
                host="file01",
                dir=mount,
                private_key_file=private_key_file,
                known_hosts_file=known_hosts_file,
                required=True,
            )
        )

    assert "read-write" in caplog.text
    assert str(mount) in caplog.text
