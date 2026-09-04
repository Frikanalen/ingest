"""The `fk-archive` command, as the engine asks for it.

The engine has no write access to the archive. Every mutation it performs is a
request to `fk-archive` on the storage host, run under sudo as the account that
owns the media, and this module is the whole of what crosses that boundary: how
a request is spelled, and how an answer is read back.

The protocol is what an SSH command invocation gives us and nothing more. One
line of JSON on stdout says what was done; a sentence on stderr says what was
refused; the exit status says which refusal it was, so a caller tells "something
is already there" apart from "the archive is full" without parsing prose.

The profile is deliberately absent from every command built here. It is the
first argument `fk-archive` takes, and the forced command on the storage host
supplies it -- which is what lets one sudoers rule pin it, and what makes it
impossible for this engine to name a different archive than the key it holds is
for. A profile sent from this side would be a profile the far side had to trust.
"""

import json
import shlex
from pathlib import PurePosixPath

from app.archive_store.base import ArchiveError, FileAlreadyArchived

#: What the far end calls itself. The forced command matches on this name
#: before it prepends the profile and hands the rest to sudo, so it is part of
#: the request rather than decoration.
COMMAND = "fk-archive"

#: Exit codes, mirrored from `fk_archive_utils.errors`. They are the interface
#: -- the two packages are deployed separately, so this is a copy of a contract
#: rather than an import, and the table in `archive-utils/README.md` is where
#: the two are kept honest. Only the codes that mean something different to a
#: caller are named; everything else is an archive that would not take the file.
ALREADY_EXISTS = 3
NOT_FOUND = 4


def publish(destination: PurePosixPath, *, size: int) -> str:
    """Ask for `size` bytes on stdin to be published at `destination`.

    The size is not optional on the far side and is not padding here: a
    truncated stream ends exactly like a complete one, so the length the caller
    promises is the only thing that can tell the two apart.
    """
    return f"{COMMAND} publish {shlex.quote(str(destination))} --size {size}"


def trash(path: PurePosixPath) -> str:
    """Ask for `path` to be taken out of the published tree."""
    return f"{COMMAND} trash {shlex.quote(str(path))}"


def interpret(command: str, returncode: int | None, stdout: bytes, stderr: bytes) -> dict:
    """What the command reported, or the exception its exit code names.

    A refusal arrives as an exception of the type the rest of this package
    already raises for that condition, so callers handle an occupied
    destination the same way whichever archive they are talking to.

    A `returncode` of None is a channel that closed without one, and a negative
    one is a process killed by a signal. Neither is a refusal these tools made,
    so neither can be read as one: they fall through to a plain archive error.
    """
    if returncode == 0:
        return _parse(command, stdout)

    message = _refusal(command, returncode, stderr)
    if returncode == ALREADY_EXISTS:
        raise FileAlreadyArchived(message)
    if returncode == NOT_FOUND:
        # The same exception a missing file raises when it is read, so a path
        # that is not there reads the same way whether it was fetched or
        # trashed.
        raise FileNotFoundError(message)
    raise ArchiveError(message)


def _parse(command: str, stdout: bytes) -> dict:
    try:
        result = json.loads(stdout)
    except ValueError as e:
        raise ArchiveError(f"{command} succeeded but said {stdout!r}, which is not the JSON it promises") from e
    if not isinstance(result, dict):
        raise ArchiveError(f"{command} succeeded but said {result!r} rather than an object")
    return result


def _refusal(command: str, returncode: int | None, stderr: bytes) -> str:
    """The sentence to raise, preferring the far end's own.

    It already names the path and what was wrong with it. The command is added
    only when there was nothing on stderr -- a process killed by a signal, say
    -- because otherwise there would be nothing at all to go and look at.
    """
    said = stderr.decode("utf-8", "replace").strip()
    return said or f"{command} exited with {returncode} and said nothing"
