import io
import os
import stat
from pathlib import Path

import pytest

from fk_archive_utils.archive_path import parse_file_path
from fk_archive_utils.errors import AlreadyExists, TransferError, UsageError
from fk_archive_utils.operations import publish

CONTENT = b"the original, as far as anyone here is concerned" * 100


def do_publish(profile, destination="12/original/a.mov", content=CONTENT, **kwargs):
    kwargs.setdefault("expected_size", len(content))
    return publish(profile, parse_file_path(destination), io.BytesIO(content), **kwargs)


def test_publishes_the_bytes_it_was_given(profile, archive_root: Path):
    result = do_publish(profile)

    assert (archive_root / "12/original/a.mov").read_bytes() == CONTENT
    assert result.bytes_written == len(CONTENT)


def test_creates_the_parents_the_destination_names(profile, archive_root: Path):
    do_publish(profile, "12/large_thumb/a.jpg")

    assert (archive_root / "12/large_thumb").is_dir()


def test_applies_the_profile_modes_rather_than_the_inherited_umask(profile, archive_root: Path):
    do_publish(profile)

    assert stat.S_IMODE((archive_root / "12/original/a.mov").stat().st_mode) == profile.file_mode
    assert stat.S_IMODE((archive_root / "12/original").stat().st_mode) == profile.dir_mode
    assert stat.S_IMODE((archive_root / "12").stat().st_mode) == profile.dir_mode


def test_refuses_to_publish_over_something_already_there(profile, archive_root: Path, make_file):
    make_file("12/original/a.mov", b"the file a reader may have open")

    with pytest.raises(AlreadyExists):
        do_publish(profile)

    assert (archive_root / "12/original/a.mov").read_bytes() == b"the file a reader may have open"


def test_a_short_transfer_publishes_nothing(profile, archive_root: Path):
    with pytest.raises(TransferError, match="expected 999999 bytes"):
        do_publish(profile, expected_size=999999)

    assert not (archive_root / "12/original").exists()


def test_a_negative_size_is_a_usage_error(profile):
    with pytest.raises(UsageError, match="size"):
        do_publish(profile, expected_size=-1)


def test_the_spool_is_left_empty_whether_it_worked_or_not(profile, archive_root: Path):
    do_publish(profile)
    with pytest.raises(TransferError):
        do_publish(profile, "12/original/b.mov", expected_size=1)

    assert os.listdir(archive_root / ".spool") == []


def test_the_spool_is_not_readable_by_anyone_else(profile, archive_root: Path):
    do_publish(profile)

    assert stat.S_IMODE((archive_root / ".spool").stat().st_mode) == 0o700


def test_an_empty_file_is_a_perfectly_good_file(profile, archive_root: Path):
    do_publish(profile, content=b"")

    assert (archive_root / "12/original/a.mov").read_bytes() == b""


def test_will_not_traverse_a_symlink_planted_in_the_archive(profile, archive_root: Path, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (archive_root / "12").symlink_to(elsewhere)

    with pytest.raises(UsageError, match="symbolic link"):
        do_publish(profile)

    assert list(elsewhere.iterdir()) == []
