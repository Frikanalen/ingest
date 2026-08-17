from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from pathlib import Path, PurePosixPath


class ArchiveError(RuntimeError):
    """The archive could not accept a file."""


class FileAlreadyArchived(ArchiveError):
    """Something already occupies the destination in the archive."""


SPOOL_DIR = PurePosixPath(".spool")


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


class ArchiveSession(ABC):
    """A live connection to the archive, held for the duration of one ingest job.

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
        """

    async def assert_absent(self, destination: PurePosixPath) -> None:
        """Raise FileAlreadyArchived if `destination` is already taken."""
        if await self.exists(destination):
            raise FileAlreadyArchived(f"{destination} already exists in the archive")


class ArchiveStore(ABC):
    """Where the archive lives.

    Cheap to construct and safe to share between jobs; the connection itself is
    per-job, since ingest jobs can overlap and each needs its own session.
    """

    @abstractmethod
    def open(self) -> AbstractAsyncContextManager[ArchiveSession]:
        """Open a session against the archive."""
