"""The env-var contract for choosing an archive.

FK_ARCHIVE_HOST is the switch: without it the archive is a local directory
ingest writes to; with it, FK_ARCHIVE_DIR is where the archive is mounted
read-only and the host is asked to perform every mutation.
"""

from pathlib import Path

import pytest

from app.util.settings import (
    DjangoApiSettingsPwdAuth,
    IngestAppSettings,
    LocalArchiveSettings,
    SshArchiveSettings,
)

API = DjangoApiSettingsPwdAuth(url="http://localhost:8000", username="ingest", password="hunter2")

ARCHIVE_ENV = (
    "FK_ARCHIVE_DIR",
    "FK_ARCHIVE_HOST",
    "FK_ARCHIVE_PORT",
    "FK_ARCHIVE_USERNAME",
    "FK_ARCHIVE_PRIVATE_KEY_FILE",
    "FK_ARCHIVE_KNOWN_HOSTS_FILE",
    "FK_ARCHIVE_FALLBACK_DIR",
    "FK_ARCHIVE_REQUIRED",
)


@pytest.fixture(autouse=True)
def clean_archive_env(monkeypatch):
    for name in ARCHIVE_ENV:
        monkeypatch.delenv(name, raising=False)


def settings() -> IngestAppSettings:
    return IngestAppSettings(_env_file=None, api=API)


def test_defaults_to_a_local_archive():
    assert settings().archive == LocalArchiveSettings(dir=Path("./archive"))


def test_archive_dir_alone_stays_local(monkeypatch):
    monkeypatch.setenv("FK_ARCHIVE_DIR", "/srv/archive")

    archive = settings().archive

    assert isinstance(archive, LocalArchiveSettings)
    assert archive.dir == Path("/srv/archive")


def test_archive_host_selects_ssh(monkeypatch):
    monkeypatch.setenv("FK_ARCHIVE_HOST", "file01")
    monkeypatch.setenv("FK_ARCHIVE_DIR", "/tank/media")
    monkeypatch.setenv("FK_ARCHIVE_USERNAME", "ingest")
    monkeypatch.setenv("FK_ARCHIVE_PRIVATE_KEY_FILE", "/etc/ingest/ssh/id_ed25519")
    monkeypatch.setenv("FK_ARCHIVE_KNOWN_HOSTS_FILE", "/etc/ingest/ssh/known_hosts")

    archive = settings().archive

    assert archive == SshArchiveSettings(
        host="file01",
        dir=Path("/tank/media"),
        username="ingest",
        private_key_file=Path("/etc/ingest/ssh/id_ed25519"),
        known_hosts_file=Path("/etc/ingest/ssh/known_hosts"),
    )


def test_ssh_archive_has_usable_defaults(monkeypatch):
    monkeypatch.setenv("FK_ARCHIVE_HOST", "file01")

    archive = settings().archive

    assert isinstance(archive, SshArchiveSettings)
    assert archive.port == 22
    assert archive.dir == Path("/archive/media")


def test_unknown_archive_settings_are_rejected(monkeypatch):
    """extra=forbid is what keeps the local/ssh union unambiguous."""
    with pytest.raises(ValueError):
        LocalArchiveSettings(dir=Path("/srv/archive"), host="file01")


def test_the_deployed_environment_parses(monkeypatch, tmp_path):
    """The env the Helm chart emits, so chart and settings cannot drift apart.

    Mirrors chart/templates/_helpers.tpl; update both together.
    """
    mount = tmp_path / "archive"
    mount.mkdir()
    private_key_file = tmp_path / "id_ed25519"
    private_key_file.touch()
    known_hosts_file = tmp_path / "known_hosts"
    known_hosts_file.touch()

    for name, value in {
        "FK_ARCHIVE_HOST": "file01",
        "FK_ARCHIVE_PORT": "22",
        "FK_ARCHIVE_USERNAME": "ingest",
        # /archive/media in the chart; a path that exists here, because this is
        # now the mount point and unusable_reason() checks the volume is there.
        "FK_ARCHIVE_DIR": str(mount),
        "FK_ARCHIVE_PRIVATE_KEY_FILE": str(private_key_file),
        "FK_ARCHIVE_KNOWN_HOSTS_FILE": str(known_hosts_file),
        "FK_ARCHIVE_REQUIRED": "true",
    }.items():
        monkeypatch.setenv(name, value)

    archive = settings().archive

    assert isinstance(archive, SshArchiveSettings)
    assert archive.required is True
    assert archive.unusable_reason() is None
