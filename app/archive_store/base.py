from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


class ArchiveError(RuntimeError):
    """The archive could not accept a file."""


class FileAlreadyArchived(ArchiveError):
    """Something already occupies the destination in the archive."""


SPOOL_DIR = PurePosixPath(".spool")

#: Where anything removed from the published tree is parked. A rename rather
#: than a delete, so an hour of deciding the rule was wrong costs a rename back
#: rather than a restore from backup. Purged separately, once someone has read
#: the report that produced it.
TRASH_DIR = PurePosixPath(".trash")

#: Directories the archive keeps for its own bookkeeping. Nothing that walks
#: the archive looking for videos should mistake one of these for a video.
RESERVED_DIRS = frozenset({SPOOL_DIR.name, TRASH_DIR.name})


def staging_path(destination: PurePosixPath) -> PurePosixPath:
    """Where a file is transferred before it is published at `destination`.

    Staging happens outside the published tree, so an interrupted transfer
    cannot leave a partial file where readers see it — the archive is exported
    read-only to the playout hosts, and a half-written video appearing there is
    worse than one that never appears.

    It stays inside the archive root because publishing is a rename, and rename
    cannot cross a filesystem boundary. Somewhere tidier but separately mounted
    would fail with EXDEV.
    """
    return SPOOL_DIR / destination


def trash_path(path: PurePosixPath, when: datetime) -> PurePosixPath:
    """Where `path` goes when it is taken out of the published tree.

    Stamped with the time it was trashed, which is what lets the same path be
    trashed more than once and what `purge-trash --older-than` reads. The
    timestamp is a directory rather than a suffix so the original path survives
    intact underneath it, and putting a thing back is a rename to where the
    name already says it came from.
    """
    return TRASH_DIR / when.strftime("%Y%m%dT%H%M%SZ") / path


@dataclass(frozen=True)
class ArchiveEntry:
    """One name directly inside a directory in the archive.

    Carries the type and size that the listing already had to look at, since
    over SFTP asking again per entry is a round trip apiece.
    """

    #: Archive-relative, so it can be handed straight back to get() or move().
    path: PurePosixPath
    is_dir: bool
    #: Bytes. Zero for a directory, whose own size means nothing here.
    size: int

    @property
    def name(self) -> str:
        return self.path.name


class ArchiveSession(ABC):
    """A live connection to the archive, held for the duration of one job.

    Destinations are always relative to the archive root, so callers never need
    to know whether the archive is a local directory or another host.
    """

    @abstractmethod
    async def exists(self, destination: PurePosixPath) -> bool:
        """Whether anything already occupies `destination`."""

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
    async def list_dir(self, directory: PurePosixPath) -> list[ArchiveEntry]:
        """What is directly inside `directory`, sorted by path.

        A directory that does not exist lists as empty rather than raising:
        every caller is asking a question about the archive's contents, and
        "nothing there" is an answer to that question rather than an error.
        """

    @abstractmethod
    async def get(self, source: PurePosixPath, destination: Path) -> None:
        """Copy the archived file `source` out to the local path `destination`.

        The reverse of put(), and staged the same way for the same reason: a
        transfer that dies partway must not leave something that later reads as
        a complete original.
        """

    @abstractmethod
    async def move(self, source: PurePosixPath, destination: PurePosixPath) -> None:
        """Rename `source` to `destination` within the archive.

        Missing parents are created. Raises FileAlreadyArchived rather than
        replacing anything at `destination`.
        """

    async def assert_absent(self, destination: PurePosixPath) -> None:
        """Raise FileAlreadyArchived if `destination` is already taken."""
        if await self.exists(destination):
            raise FileAlreadyArchived(f"{destination} already exists in the archive")

    async def trash(self, path: PurePosixPath) -> PurePosixPath:
        """Take `path` out of the published tree, and say where it went.

        Built on move() so both archives cannot drift on what deletion means,
        and so nothing in this codebase has a way to actually destroy archived
        media — purging the trash is a separate, deliberate act.
        """
        destination = trash_path(path, datetime.now(UTC))
        await self.move(path, destination)
        return destination


class ArchiveStore(ABC):
    """Where the archive lives.

    Cheap to construct and safe to share between jobs; the connection itself is
    per-job, since ingest jobs can overlap and each needs its own session.
    """

    @abstractmethod
    def open(self) -> AbstractAsyncContextManager[ArchiveSession]:
        """Open a session against the archive."""
