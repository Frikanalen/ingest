from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ArchiveError(RuntimeError):
    """The archive could not accept a file."""


class FileAlreadyArchived(ArchiveError):
    """Something already occupies the destination in the archive."""


#: Where anything removed from the published tree is parked. A rename rather
#: than a delete, so an hour of deciding the rule was wrong costs a rename back
#: rather than a restore from backup. Purged separately, once someone has read
#: the report that produced it.
TRASH_DIR = PurePosixPath(".trash")


#: How a trash directory is stamped, and what `fk-archive-purge-trash
#: --older-than` parses back out of it. Both archives spell it the same way,
#: because the tool that purges the trash reads it off the name.
TRASH_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def check_removable(path: PurePosixPath) -> PurePosixPath:
    """Refuse anything that is not a whole video or one directory inside one.

    Those are the only two things anything has ever asked to remove: a video
    goes when the catalogue no longer has a row for it, and a directory goes
    when an upload supersedes the media under it or a format is rebuilt.

    Restated from `fk-archive`, which refuses the same shapes in its argument
    parser before it looks at the archive at all. Restated rather than left to
    the far end because a local archive that accepted a file path would let
    code be written against it that file01 then declines to run.
    """
    if len(path.parts) not in (1, 2):
        raise ArchiveError(f"{path} is neither a video nor a directory inside one, so it cannot be trashed")
    return path


def trash_path(path: PurePosixPath, stamp: str) -> PurePosixPath:
    """Where `path` goes when it is taken out of the published tree.

    Stamped with the time it was trashed, which is what lets the same path be
    trashed more than once and what `purge-trash --older-than` reads. The
    timestamp is a directory rather than a suffix so the original path survives
    intact underneath it, and putting a thing back is a rename to where the
    name already says it came from.
    """
    return TRASH_DIR / stamp / path


@dataclass(frozen=True)
class ArchiveEntry:
    """One name directly inside a directory in the archive.

    Carries whether it is a directory, because that is the one thing every
    caller asks and the listing had to look at anyway. Nothing asks how big a
    file is, so nothing goes and finds out.
    """

    #: Archive-relative, so it can be handed straight back to get() or trash().
    path: PurePosixPath
    is_dir: bool

    @property
    def name(self) -> str:
        return self.path.name


class ArchiveReader(ABC):
    """Everything the archive can be asked, and nothing it can be asked to do.

    Reading and writing arrive by different routes in a deployment -- the
    archive is mounted read-only and mutated by asking a privileged command --
    so they are different interfaces here too. A caller that only looks at the
    archive says so by taking this, and then holds nothing it could alter the
    archive with even by mistake.

    Paths are always relative to the archive root, so callers never need to
    know whether the archive is a local directory or a mounted export.
    """

    @abstractmethod
    async def exists(self, destination: PurePosixPath) -> bool:
        """Whether anything already occupies `destination`."""

    @abstractmethod
    async def list_dir(self, directory: PurePosixPath) -> list[ArchiveEntry]:
        """What is directly inside `directory`, sorted by path.

        A directory that does not exist lists as empty rather than raising:
        every caller is asking a question about the archive's contents, and
        "nothing there" is an answer to that question rather than an error.
        """

    @abstractmethod
    async def get(self, source: PurePosixPath, destination: Path) -> None:
        """Copy the archived file `source` out to the local path `destination`.

        Copied rather than read in place, and staged under a temporary name
        while it is: ffmpeg reads its input many times over the course of a
        job, and a mount that hiccups an hour into an encode should cost a
        retry of the copy rather than the encode.
        """

    async def assert_absent(self, destination: PurePosixPath) -> None:
        """Raise FileAlreadyArchived if `destination` is already taken."""
        if await self.exists(destination):
            raise FileAlreadyArchived(f"{destination} already exists in the archive")


class ArchiveSession(ArchiveReader, ABC):
    """A reader that can also be asked to change what it reads.

    Held for the duration of one job. In a deployment the two halves reach the
    archive by different routes and as different accounts, which is the reason
    the interfaces are separate -- see `SshArchiveSession`.
    """

    @abstractmethod
    async def put(self, source: Path, destination: PurePosixPath) -> None:
        """Copy the local file `source` into the archive at `destination`.

        Missing parent directories are created. The file is transferred under a
        temporary name and only published at `destination` once it has arrived
        in full, so an interrupted transfer cannot leave a truncated file behind
        that later looks like a complete one.

        Raises FileAlreadyArchived rather than replacing anything already there.
        Publishing over a file is never what a caller meant: a rebuild swaps the
        whole format directory instead, because a new profile can emit a
        different set of files and overwriting one by one would leave the old
        profile's leftovers beside the new output forever.
        """

    @abstractmethod
    async def trash(self, path: PurePosixPath) -> PurePosixPath:
        """Take `path` out of the published tree, and say where it went.

        A rename into `trash_path()`, never a delete: nothing in this codebase
        has a way to destroy archived media, so a rule that turns out to be
        wrong costs a rename back rather than a restore from backup. Purging is
        a separate and deliberate act, and lives on the storage host.

        `path` is a whole video or one directory inside one, which is all
        anything has ever asked to remove. Raises FileNotFoundError if there is
        nothing there.

        Implemented per backend rather than built on a general rename, because
        one of the two backends has no general rename to build on: the SSH
        archive asks a privileged command to do this and is told where it
        landed. A move it could compose out of would be a move it could point
        anywhere.
        """


class ArchiveStore(ABC):
    """Where the archive lives.

    Cheap to construct and safe to share between jobs; the connection itself is
    per-job, since ingest jobs can overlap and each needs its own session.
    """

    @abstractmethod
    def open(self) -> AbstractAsyncContextManager[ArchiveSession]:
        """Open a session against the archive."""
