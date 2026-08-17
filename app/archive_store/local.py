import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path, PurePosixPath

from app.archive_store.base import ArchiveError, ArchiveSession, ArchiveStore, partial_path

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
        partial = self.resolve(partial_path(destination))

        logger.info("Copying %s to %s", source, target)
        target.parent.mkdir(parents=True, exist_ok=True)

        # copy2 is blocking and the files are large, so keep it off the event loop.
        await asyncio.to_thread(shutil.copy2, source, partial)
        partial.replace(target)


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
