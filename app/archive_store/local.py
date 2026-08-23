import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path, PurePosixPath

from app.archive_store.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveSession,
    ArchiveStore,
    FileAlreadyArchived,
    staging_path,
)

logger = getLogger(__name__)


class LocalArchiveSession(ArchiveSession):
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, destination: PurePosixPath) -> Path:
        return self.root / destination

    async def exists(self, destination: PurePosixPath) -> bool:
        return self.resolve(destination).exists()

    async def put(self, source: Path, destination: PurePosixPath) -> None:
        target = self.resolve(destination)
        staged = self.resolve(staging_path(destination))

        logger.info("Copying %s to %s", source, target)
        staged.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        # copy2 is blocking and the files are large, so keep it off the event loop.
        await asyncio.to_thread(shutil.copy2, source, staged)

        # Checked, not atomic: POSIX rename replaces silently, and there is no
        # portable no-clobber rename to reach for. The SSH archive gets this for
        # free from SFTP and is the one that matters; here the check is what
        # keeps the two backends answering the same way, so a republish fails in
        # development exactly as it would against file01. The failed transfer is
        # left in the spool rather than cleaned up, which is also what SFTP does.
        if target.exists():
            raise FileAlreadyArchived(f"{destination} already exists in the archive")

        staged.replace(target)
        self.tidy_spool(staged.parent)

    async def list_dir(self, directory: PurePosixPath) -> list[ArchiveEntry]:
        resolved = self.resolve(directory)
        if not resolved.is_dir():
            return []

        entries = []
        for child in resolved.iterdir():
            is_dir = child.is_dir()
            entries.append(
                ArchiveEntry(
                    path=directory / child.name,
                    is_dir=is_dir,
                    size=0 if is_dir else child.stat().st_size,
                )
            )
        return sorted(entries, key=lambda entry: entry.path)

    async def get(self, source: PurePosixPath, destination: Path) -> None:
        origin = self.resolve(source)
        staged = destination.with_name(destination.name + ".part")

        logger.info("Copying %s to %s", origin, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(shutil.copy2, origin, staged)
        staged.replace(destination)

    async def move(self, source: PurePosixPath, destination: PurePosixPath) -> None:
        origin = self.resolve(source)
        target = self.resolve(destination)

        if target.exists():
            raise FileAlreadyArchived(f"{destination} already exists in the archive")

        logger.info("Moving %s to %s", origin, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        origin.rename(target)

    def tidy_spool(self, directory: Path) -> None:
        """Remove the staging directories the transfer just emptied.

        Best effort: a directory still holding something belongs to a
        concurrent job, and leaving it is harmless.
        """
        while directory != self.root:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent


class LocalArchiveStore(ArchiveStore):
    """Archive on a filesystem this process can write to directly."""

    def __init__(self, root: Path):
        if not root.is_dir():
            raise ArchiveError(f"Archive directory {root} does not exist")
        self.root = root

    @asynccontextmanager
    async def open(self) -> AsyncIterator[ArchiveSession]:
        yield LocalArchiveSession(self.root)

    def __str__(self) -> str:
        return str(self.root)
