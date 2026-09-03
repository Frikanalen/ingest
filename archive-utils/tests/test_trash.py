import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fk_archive_utils.archive_path import parse_removable_path
from fk_archive_utils.errors import NotFound
from fk_archive_utils.operations import trash

WHEN = datetime(2026, 9, 3, 12, 13, 14, tzinfo=UTC)


def do_trash(profile, path="12/dash", when=WHEN):
    return trash(profile, parse_removable_path(path), now=when)


def test_a_format_directory_is_renamed_rather_than_deleted(profile, archive_root: Path, make_file):
    make_file("12/dash/manifest.mpd", b"a manifest")

    result = do_trash(profile)

    assert result.destination == ".trash/20260903T121314Z/12/dash"
    assert (archive_root / result.destination / "manifest.mpd").read_bytes() == b"a manifest"
    assert not (archive_root / "12/dash").exists()


def test_a_whole_video_goes_when_the_catalogue_has_dropped_it(profile, archive_root: Path, make_file):
    make_file("12/original/a.mov")
    make_file("12/images/cover.jpg")

    result = do_trash(profile, "12")

    assert result.destination == ".trash/20260903T121314Z/12"
    assert (archive_root / ".trash/20260903T121314Z/12/images/cover.jpg").exists()
    assert not (archive_root / "12").exists()


def test_two_removals_in_the_same_second_do_not_collide(profile, archive_root: Path, make_file):
    make_file("12/dash/manifest.mpd")
    make_file("12/large_thumb/a.jpg")

    first = do_trash(profile, "12/dash")
    second = do_trash(profile, "12/large_thumb")

    assert first.destination == ".trash/20260903T121314Z/12/dash"
    assert second.destination == ".trash/20260903T121314Z.1/12/large_thumb"
    assert (archive_root / second.destination / "a.jpg").exists()


def test_the_same_path_can_be_trashed_more_than_once(profile, archive_root: Path, make_file):
    make_file("12/dash/manifest.mpd", b"first")
    do_trash(profile)
    make_file("12/dash/manifest.mpd", b"second")

    result = do_trash(profile, when=datetime(2026, 9, 4, 1, 2, 3, tzinfo=UTC))

    assert (archive_root / result.destination / "manifest.mpd").read_bytes() == b"second"


def test_says_so_when_there_is_nothing_to_trash(profile):
    with pytest.raises(NotFound):
        do_trash(profile)


def test_nothing_is_unlinked(profile, archive_root: Path, make_file):
    make_file("12/dash/manifest.mpd", b"a manifest")

    do_trash(profile)

    survivors = [name for _, _, names in os.walk(archive_root / ".trash") for name in names]
    assert survivors == ["manifest.mpd"]
