"""The contract between the engine and `fk-archive`, tested from this side.

`tests/test_archive_store_ssh.py` drives the real command over a real
connection, which is what proves the two agree. This is the other half: what
the engine does with an answer it could not have produced itself -- an exit
code from a version it was not built against, a truncated stdout, a channel
that closed without a status at all.
"""

from pathlib import PurePosixPath

import pytest

from app.archive_store import fk_archive
from app.archive_store.base import ArchiveError, FileAlreadyArchived
from fk_archive_utils import errors

DESTINATION = PurePosixPath("12345/original/example_video.mp4")


def test_the_profile_is_never_sent():
    """The forced command on the storage host supplies it.

    Sending one would be sending something the far end had to trust, and would
    void the sudoers rule that pins staging to staging's archive.
    """
    assert fk_archive.publish(DESTINATION, size=1).split()[:2] == ["fk-archive", "publish"]
    assert fk_archive.trash(PurePosixPath("12345")).split() == ["fk-archive", "trash", "12345"]


def test_a_destination_is_quoted_for_the_shell_the_far_end_splits_with():
    """`fk-archive-ssh` splits the request with shlex before dispatching it.

    Member filenames are sanitised long before they get here, so this is a
    belt: a space arriving unquoted would become two arguments, and the second
    of them would be read as an option.
    """
    command = fk_archive.publish(PurePosixPath("12345/original/two words.mp4"), size=7)

    assert command == "fk-archive publish '12345/original/two words.mp4' --size 7"


def test_the_promised_size_travels_with_the_request():
    assert fk_archive.publish(DESTINATION, size=4823).endswith("--size 4823")


def test_an_occupied_destination_is_the_exception_callers_already_handle():
    with pytest.raises(FileAlreadyArchived, match="already in the archive"):
        fk_archive.interpret(
            "fk-archive publish x", errors.AlreadyExists.exit_code, b"", b"x is already in the archive"
        )


def test_a_missing_path_reads_the_same_as_a_missing_file():
    """Whether it was fetched or trashed, "it is not there" is one condition."""
    with pytest.raises(FileNotFoundError):
        fk_archive.interpret("fk-archive trash 99999", errors.NotFound.exit_code, b"", b"99999 is not in the archive")


@pytest.mark.parametrize(
    "returncode",
    [
        errors.ArchiveUtilsError.exit_code,  # ENOSPC, EROFS and friends
        errors.UsageError.exit_code,
        errors.TransferError.exit_code,
        errors.ProfileError.exit_code,
    ],
)
def test_every_other_refusal_is_an_archive_that_would_not_take_the_file(returncode):
    with pytest.raises(ArchiveError, match="the archive said no"):
        fk_archive.interpret("fk-archive publish x", returncode, b"", b"fk-archive: the archive said no")


def test_an_unknown_exit_code_is_not_mistaken_for_a_refusal_we_know():
    """A newer fk-archive can grow codes this engine has never heard of."""
    with pytest.raises(ArchiveError) as raised:
        fk_archive.interpret("fk-archive publish x", 42, b"", b"fk-archive: something new")

    assert not isinstance(raised.value, FileAlreadyArchived)


def test_a_channel_that_closed_without_a_status_says_which_command_it_was():
    """There is no stderr to quote, so the request is all there is to go on."""
    with pytest.raises(ArchiveError, match="fk-archive trash 12345"):
        fk_archive.interpret("fk-archive trash 12345", None, b"", b"")


def test_success_is_read_off_stdout():
    result = fk_archive.interpret("fk-archive trash 12345", 0, b'{"destination": ".trash/x/12345"}\n', b"")

    assert result["destination"] == ".trash/x/12345"


def test_success_that_did_not_say_anything_readable_is_still_a_failure():
    """A truncated answer is not a publish this engine may report as done."""
    with pytest.raises(ArchiveError, match="not the JSON it promises"):
        fk_archive.interpret("fk-archive publish x", 0, b'{"destination": ', b"")
