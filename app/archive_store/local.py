import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path, PurePosixPath

from app.archive_store.base import (
    TRASH_DIR,
    TRASH_STAMP_FORMAT,
    ArchiveError,
    ArchiveSession,
    ArchiveStore,
    FileAlreadyArchived,
    check_deletable,
    check_removable,
    trash_path,
)
from app.archive_store.directory import DirectoryReader

logger = getLogger(__name__)

#: Where a copy lands before it is published. Named here rather than in the
#: interface because the SSH archive's spool is on the far side of the fence
#: and is the privileged command's business: nothing in this process stages
#: anything there, and nothing in this process could.
SPOOL_DIR = PurePosixPath(".spool")


def staging_path(destination: PurePosixPath) -> PurePosixPath:
    """Where a file is copied before it is published at `destination`.

    Staging happens outside the published tree, so an interrupted copy cannot
    leave a partial file where readers see it.

    It stays inside the archive root because publishing is a rename, and rename
    cannot cross a filesystem boundary. Somewhere tidier but separately mounted
    would fail with EXDEV.
    """
    return SPOOL_DIR / destination


class LocalArchiveSession(DirectoryReader, ArchiveSession):
    """The development archive: one directory, read and written in place.

    Deliberately as strict as the storage host about what it will accept, since
    it is what code gets written against. A local archive that published over a
    file, or trashed one, would let something be written here that file01 then
    declines to run.
    """

    async def put(self, source: Path, destination: PurePosixPath) -> None:
        target = self.resolve(destination)
        staged = self.resolve(staging_path(destination))

        logger.info("Copying %s to %s", source, target)
        staged.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        # copy2 is blocking and the files are large, so keep it off the event loop.
        await asyncio.to_thread(shutil.copy2, source, staged)

        # Checked, not atomic: POSIX rename replaces silently, and there is no
        # portable no-clobber rename to reach for. The SSH archive gets this
        # properly -- fk-archive links the name into place, and link(2) fails
        # with EEXIST -- and is the one that matters; here the check is what
        # keeps the two backends answering the same way, so a republish fails in
        # development exactly as it would against file01.
        try:
            if target.exists():
                raise FileAlreadyArchived(f"{destination} already exists in the archive")
            staged.replace(target)
        finally:
            # Cleared whichever way this went, which is what fk-archive does
            # with its own staged copy: on success the published file is the
            # same bytes, and on failure the caller still holds what it was
            # sending, so a partial here would only fill a directory nobody
            # ever looks in.
            staged.unlink(missing_ok=True)
            self.tidy_spool(staged.parent)

    async def trash(self, path: PurePosixPath) -> PurePosixPath:
        """Rename `path` under `.trash/<stamp>/`, the way fk-archive does.

        Each call gets a stamp directory it created itself, which is what makes
        the rename into it safe without a no-clobber rename: nothing else can
        already be inside a directory this call has just made. That is also how
        the privileged command does it, and the two are meant to be
        indistinguishable from a caller's side.
        """
        origin = self.resolve(check_removable(path))
        if not origin.exists():
            raise FileNotFoundError(f"{path} is not in the archive")

        destination = trash_path(path, self._unique_stamp(datetime.now(UTC).strftime(TRASH_STAMP_FORMAT)))
        target = self.resolve(destination)

        logger.info("Trashing %s to %s", origin, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        origin.rename(target)
        return destination

    async def delete_variant(self, variant: str, video_id: str) -> bool:
        """Destroy one variant outright. False means it was already gone.

        The development counterpart of the privileged command, and refusing the
        same variants for the same reason: a rule enforced only on file01 is a
        rule this half of the codebase could be written against and then fail
        against in production.
        """
        check_deletable(variant)
        target = self.resolve(PurePosixPath(video_id) / variant)
        if not target.exists():
            return False

        logger.info("Deleting %s", target)
        # rmtree rather than a walk of our own, matching fk-archive: CPython's
        # is descriptor-based and does not follow symlinks on the way down.
        await asyncio.to_thread(shutil.rmtree, target)
        return True

    def _unique_stamp(self, base: str) -> str:
        """A stamp directory this call owns outright.

        Two removals in the same second are ordinary -- superseding an upload
        trashes every format directory in a row -- so the second one takes
        `<stamp>.1`. Purging reads the timestamp off the front of the name, so
        a suffix costs it nothing.
        """
        trash = self.resolve(TRASH_DIR)
        trash.mkdir(parents=True, exist_ok=True)
        for suffix in range(1000):
            candidate = base if suffix == 0 else f"{base}.{suffix}"
            try:
                (trash / candidate).mkdir()
            except FileExistsError:
                continue
            return candidate
        raise ArchiveError(f"could not find an unused trash directory for {base}")

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
