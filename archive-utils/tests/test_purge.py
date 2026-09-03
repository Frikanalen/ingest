from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fk_archive_utils.errors import UsageError
from fk_archive_utils.operations import purge

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def stamped(archive_root: Path, days_ago: float, name: str = "12/dash") -> Path:
    stamp = (NOW - timedelta(days=days_ago)).strftime("%Y%m%dT%H%M%SZ")
    entry = archive_root / ".trash" / stamp / name
    entry.mkdir(parents=True)
    (entry / "manifest.mpd").write_bytes(b"a manifest")
    return archive_root / ".trash" / stamp


def test_removes_only_what_has_been_there_long_enough(profile, archive_root: Path):
    old = stamped(archive_root, days_ago=40)
    recent = stamped(archive_root, days_ago=3)

    purged = purge(profile, older_than_days=30, now=NOW)

    assert [candidate.name for candidate in purged] == [old.name]
    assert not old.exists()
    assert recent.exists()


def test_a_dry_run_removes_nothing(profile, archive_root: Path):
    old = stamped(archive_root, days_ago=40)

    purged = purge(profile, older_than_days=30, now=NOW, dry_run=True)

    assert [candidate.name for candidate in purged] == [old.name]
    assert (old / "12/dash/manifest.mpd").exists()


def test_leaves_alone_anything_it_did_not_stamp(profile, archive_root: Path):
    stranger = archive_root / ".trash" / "restored-by-hand"
    stranger.mkdir(parents=True)

    assert purge(profile, older_than_days=0, now=NOW) == []
    assert stranger.exists()


def test_an_archive_that_has_never_trashed_anything_is_not_an_error(profile):
    assert purge(profile, older_than_days=30, now=NOW) == []


def test_a_negative_age_is_a_usage_error(profile):
    with pytest.raises(UsageError, match="older-than"):
        purge(profile, older_than_days=-1, now=NOW)


def test_a_suffixed_stamp_is_read_by_its_timestamp(profile, archive_root: Path):
    stamp = (NOW - timedelta(days=40)).strftime("%Y%m%dT%H%M%SZ")
    entry = archive_root / ".trash" / f"{stamp}.1" / "12" / "dash"
    entry.mkdir(parents=True)

    purged = purge(profile, older_than_days=30, now=NOW)

    assert [candidate.name for candidate in purged] == [f"{stamp}.1"]
