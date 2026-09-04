"""Reading the archive as a directory, which is how both backends read it.

In a deployment the archive is an NFS export mounted read-only into every pod,
so a listing is a `readdir` and fetching an original is a `copy2`. Locally it
is the development archive, and the same code answers.

That leaves the storage host reached over SSH for one thing only -- asking
`fk-archive` to perform a mutation -- and nothing on the read path depends on
the archive host being up, on a key, or on a connection at all.

The mount must be read-only. It is what makes the arrangement in
`archive-utils/` mean anything: writes go through a named command run as
another account precisely so this process holds no way to alter the archive,
and a writable mount hands it one back.
"""

import asyncio
import shutil
from logging import getLogger
from pathlib import Path, PurePosixPath

from app.archive_store.base import ArchiveEntry, ArchiveReader

logger = getLogger(__name__)


class DirectoryReader(ArchiveReader):
    """The archive as a directory this process can read."""

    def __init__(self, root: Path):
        self.root = root

    def resolve(self, path: PurePosixPath) -> Path:
        return self.root / path

    async def exists(self, destination: PurePosixPath) -> bool:
        return self.resolve(destination).exists()

    async def list_dir(self, directory: PurePosixPath) -> list[ArchiveEntry]:
        resolved = self.resolve(directory)
        if not resolved.is_dir():
            return []

        entries = [ArchiveEntry(path=directory / child.name, is_dir=child.is_dir()) for child in resolved.iterdir()]
        return sorted(entries, key=lambda entry: entry.path)

    async def get(self, source: PurePosixPath, destination: Path) -> None:
        origin = self.resolve(source)
        staged = destination.with_name(destination.name + ".part")

        logger.info("Copying %s to %s", origin, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # copy2 is blocking and the files are large, so keep it off the event
        # loop. Over NFS it is also slow in a way a local copy is not, which is
        # the whole reason it happens once per job rather than per format.
        await asyncio.to_thread(shutil.copy2, origin, staged)
        staged.replace(destination)
