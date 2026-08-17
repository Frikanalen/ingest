"""Translating the path tusd reports into one ingest can open.

tusd reports absolute paths as its own container sees them. That is only the
same as FK_TUSD_DIR when both mount the upload volume alike, which the Helm
chart arranges but a bare docker-compose need not.
"""

from pathlib import Path, PurePosixPath

import pytest

from app.util.settings import DjangoApiSettingsPwdAuth, IngestAppSettings

API = DjangoApiSettingsPwdAuth(url="http://localhost:8000", username="ingest", password="hunter2")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("FK_TUSD_DIR", "FK_TUSD_UPLOAD_DIR"):
        monkeypatch.delenv(name, raising=False)


def settings() -> IngestAppSettings:
    return IngestAppSettings(_env_file=None, api=API)


def resolve(reported: str, config: IngestAppSettings) -> Path:
    """What app/api/hooks/routes.py does with a post-finish storage path."""
    return config.tusd_dir / Path(reported).relative_to(config.tusd_upload_dir)


def test_defaults_match_the_historic_hardcoded_prefix():
    assert settings().tusd_upload_dir == PurePosixPath("/upload")


def test_translates_a_reported_path_to_the_local_one(monkeypatch):
    monkeypatch.setenv("FK_TUSD_DIR", "./upload")

    assert resolve("/upload/12345/video.mp4", settings()) == Path("upload/12345/video.mp4")


def test_handles_containers_mounting_at_the_same_path(monkeypatch):
    """What the chart produces: both containers mount the volume identically."""
    monkeypatch.setenv("FK_TUSD_DIR", "/upload")
    monkeypatch.setenv("FK_TUSD_UPLOAD_DIR", "/upload")

    assert resolve("/upload/12345/video.mp4", settings()) == Path("/upload/12345/video.mp4")


def test_handles_containers_mounting_at_different_paths(monkeypatch):
    monkeypatch.setenv("FK_TUSD_DIR", "/srv/incoming")
    monkeypatch.setenv("FK_TUSD_UPLOAD_DIR", "/data")

    assert resolve("/data/12345/video.mp4", settings()) == Path("/srv/incoming/12345/video.mp4")


def test_rejects_a_path_from_outside_the_upload_directory(monkeypatch):
    """A mismatch must fail loudly rather than resolve somewhere unintended."""
    monkeypatch.setenv("FK_TUSD_DIR", "/upload")
    monkeypatch.setenv("FK_TUSD_UPLOAD_DIR", "/data")

    with pytest.raises(ValueError):
        resolve("/somewhere/else/12345/video.mp4", settings())
