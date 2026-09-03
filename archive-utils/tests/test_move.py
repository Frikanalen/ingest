from pathlib import Path

import pytest

from fk_archive_utils.archive_path import parse_file_path
from fk_archive_utils.errors import AlreadyExists, NotFound, UsageError
from fk_archive_utils.operations import move


def do_move(profile, source="12/broadcast/a.mov", destination="12/original/a.mov"):
    return move(profile, parse_file_path(source), parse_file_path(destination))


def test_moves_the_source_where_the_legacy_migration_wants_it(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov", b"the source, under the old name")

    result = do_move(profile)

    assert (archive_root / "12/original/a.mov").read_bytes() == b"the source, under the old name"
    assert not (archive_root / "12/broadcast/a.mov").exists()
    assert result.destination == "12/original/a.mov"


def test_creates_the_destination_directory(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov")

    do_move(profile)

    assert (archive_root / "12/original").is_dir()


def test_refuses_to_move_over_something(profile, archive_root: Path, make_file):
    make_file("12/broadcast/a.mov", b"old")
    make_file("12/original/a.mov", b"the one everything already points at")

    with pytest.raises(AlreadyExists):
        do_move(profile)

    assert (archive_root / "12/original/a.mov").read_bytes() == b"the one everything already points at"
    assert (archive_root / "12/broadcast/a.mov").exists()


def test_refuses_to_move_between_videos(profile, make_file):
    make_file("12/broadcast/a.mov")

    with pytest.raises(UsageError, match="inside one video"):
        do_move(profile, destination="13/original/a.mov")


def test_refuses_a_move_that_goes_nowhere(profile, make_file):
    make_file("12/broadcast/a.mov")

    with pytest.raises(UsageError, match="same path"):
        do_move(profile, destination="12/broadcast/a.mov")


def test_refuses_to_move_something_that_is_not_a_regular_file(profile, archive_root: Path):
    (archive_root / "12/broadcast/a.mov").mkdir(parents=True)

    with pytest.raises(UsageError, match="not a regular file"):
        do_move(profile)


def test_refuses_to_move_a_symlink(profile, archive_root: Path, make_file):
    make_file("12/original/real.mov", b"the real thing")
    (archive_root / "12/broadcast").mkdir()
    (archive_root / "12/broadcast/a.mov").symlink_to(archive_root / "12/original/real.mov")

    with pytest.raises(UsageError, match="not a regular file"):
        do_move(profile)


def test_says_so_when_there_is_nothing_to_move(profile):
    with pytest.raises(NotFound):
        do_move(profile)
