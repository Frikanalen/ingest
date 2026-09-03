"""The one-shot broadcast/ migration.

The decisions here are the ones the ingest engine's backfill chore used to
make, so these are deliberately the same five cases its tests covered: this is
where that behaviour lives now.
"""

import io
from pathlib import Path

import pytest

from fk_archive_utils import migrate_broadcast
from fk_archive_utils.catalogue import Credentials
from fk_archive_utils.errors import CatalogueError, UsageError
from fk_archive_utils.migrate_broadcast import find_candidates, migrate_video, run


def _fake_credentials(environment, *, config_path=None, api_url=None) -> Credentials:
    return Credentials(api_url="http://catalogue.invalid", token="unused", environment=environment)


class FakeCatalogue:
    """Stands in for django-api, and records what was asked of it."""

    def __init__(self, videos: dict[str, list[dict]], *, dry_run: bool = False):
        self.videos = videos
        self.dry_run = dry_run
        self.retagged: list[tuple[int, str, str]] = []
        self.unregistered: list[int] = []

    def video_exists(self, video_id: str) -> bool:
        return video_id in self.videos

    def files_for_video(self, video_id: str) -> list[dict]:
        return self.videos[video_id]

    def retag(self, file_id: int, *, variant: str, filename: str) -> None:
        if not self.dry_run:
            self.retagged.append((file_id, variant, filename))

    def unregister(self, file_id: int) -> None:
        if not self.dry_run:
            self.unregistered.append(file_id)


def row(file_id: int, variant: str, filename: str) -> dict:
    return {"id": file_id, "variant": variant, "filename": filename}


def test_broadcast_is_promoted_to_original_and_the_rows_follow(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov", b"the source, under the old name")
    catalogue = FakeCatalogue({"12": [row(7, "broadcast", "12/broadcast/a.mov")]})

    report = migrate_video(profile, catalogue, "12", apply=True)

    assert (archive_root / "12/original/a.mov").read_bytes() == b"the source, under the old name"
    assert catalogue.retagged == [(7, "original", "12/original/a.mov")]
    assert report.trashed.startswith(".trash/")
    assert not (archive_root / "12/broadcast").exists()


def test_a_row_recorded_under_some_other_prefix_is_retagged_by_its_basename(profile, make_file):
    make_file("12/broadcast/a.mov")
    catalogue = FakeCatalogue({"12": [row(7, "broadcast", "old/wherever/a.mov")]})

    migrate_video(profile, catalogue, "12", apply=True)

    assert catalogue.retagged == [(7, "original", "12/original/a.mov")]


def test_a_redundant_broadcast_copy_is_trashed_and_its_rows_dropped(profile, archive_root: Path, make_file):
    make_file("12/original/a.mov", b"the one everything already points at")
    make_file("12/broadcast/a.mov", b"the copy")
    catalogue = FakeCatalogue({"12": [row(7, "broadcast", "12/broadcast/a.mov")]})

    report = migrate_video(profile, catalogue, "12", apply=True)

    assert (archive_root / "12/original/a.mov").read_bytes() == b"the one everything already points at"
    assert catalogue.unregistered == [7]
    assert catalogue.retagged == []
    assert (archive_root / report.trashed / "a.mov").read_bytes() == b"the copy"


def test_media_no_row_claims_is_left_where_it_is(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov")
    catalogue = FakeCatalogue({"12": []})

    report = migrate_video(profile, catalogue, "12", apply=True)

    assert "no videofile row" in report.outcome
    assert (archive_root / "12/broadcast/a.mov").exists()
    assert catalogue.retagged == []


def test_a_video_the_catalogue_has_dropped_is_left_for_the_gc(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov")
    catalogue = FakeCatalogue({})

    report = migrate_video(profile, catalogue, "12", apply=True)

    assert "not in the catalogue" in report.outcome
    assert (archive_root / "12/broadcast/a.mov").exists()


def test_an_empty_broadcast_directory_is_left_alone(profile, archive_root: Path):
    (archive_root / "12/broadcast").mkdir(parents=True)
    catalogue = FakeCatalogue({"12": []})

    report = migrate_video(profile, catalogue, "12", apply=True)

    assert "holds no files" in report.outcome
    assert (archive_root / "12/broadcast").exists()


def test_without_apply_nothing_is_changed(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov", b"untouched")
    catalogue = FakeCatalogue({"12": [row(7, "broadcast", "12/broadcast/a.mov")]}, dry_run=True)

    report = migrate_video(profile, catalogue, "12", apply=False)

    assert report.moved == ["12/broadcast/a.mov"]
    assert report.retagged == [7]
    assert (archive_root / "12/broadcast/a.mov").read_bytes() == b"untouched"
    assert not (archive_root / "12/original").exists()
    assert catalogue.retagged == []


def test_candidates_are_the_videos_with_a_broadcast_directory(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov")
    make_file("3/broadcast/a.mov")
    make_file("40/original/a.mov")
    (archive_root / ".trash").mkdir()

    assert find_candidates(profile) == ["3", "12"]


def test_one_video_failing_does_not_stop_the_run(profile_dir: Path, make_file, monkeypatch):
    """A run over the whole catalogue is worth more than one that stops early."""
    make_file("12/broadcast/a.mov")
    make_file("13/broadcast/a.mov")

    def explode(profile, catalogue, video_id, *, apply):
        if video_id == "12":
            raise CatalogueError("the catalogue said no")
        return migrate_broadcast.VideoReport(video_id, "left alone")

    monkeypatch.setattr(migrate_broadcast, "load_credentials", _fake_credentials)
    monkeypatch.setattr(migrate_broadcast, "migrate_video", explode)
    out = io.StringIO()

    code = run(["test", "--apply"], stdout=out, profile_dir=profile_dir)

    assert code == 1
    assert "FAILED: the catalogue said no" in out.getvalue()
    assert "video 13: left alone" in out.getvalue()
    assert "2 videos considered, 1 failed" in out.getvalue()


def test_the_run_refuses_a_video_id_that_is_not_one(profile_dir: Path):
    out = io.StringIO()

    code = run(["test", "--video", "../etc"], stdout=out, profile_dir=profile_dir)

    assert code == UsageError.exit_code


def test_a_missing_token_says_to_log_in_with_fk_cli(tmp_path: Path):
    from fk_archive_utils.catalogue import load_credentials

    config = tmp_path / "frikanalen.yaml"
    config.write_text("environment: staging\nenvironments:\n  staging:\n    api: http://localhost:8000\n")

    with pytest.raises(CatalogueError, match="Log in to prod with fk-cli"):
        load_credentials("prod", config_path=str(config))


def test_the_environment_defaults_to_the_profile_rather_than_the_selected_one(tmp_path: Path):
    from fk_archive_utils.catalogue import load_credentials

    config = tmp_path / "frikanalen.yaml"
    config.write_text(
        "environment: staging\n"
        "environments:\n"
        "  staging:\n    api: http://staging\n    token: s\n"
        "  prod:\n    api: http://prod\n    token: p\n"
    )

    credentials = load_credentials("prod", config_path=str(config))

    assert (credentials.api_url, credentials.environment) == ("http://prod", "prod")


def test_a_configuration_file_that_is_not_there_says_so(tmp_path: Path):
    from fk_archive_utils.catalogue import load_credentials

    with pytest.raises(CatalogueError, match="Log in with fk-cli"):
        load_credentials("prod", config_path=str(tmp_path / "nope.yaml"))
