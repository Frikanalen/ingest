from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path, PurePosixPath
from time import monotonic

import asyncssh

from app.archive_store.base import ArchiveError, ArchiveSession, ArchiveStore, staging_path
from app.util.pretty_duration import pretty_duration
from app.util.settings import SshArchiveSettings

logger = getLogger(__name__)


class SshArchiveSession(ArchiveSession):
    def __init__(self, sftp: asyncssh.SFTPClient, root: PurePosixPath):
        self.sftp = sftp
        self.root = root

    def resolve(self, destination: PurePosixPath) -> PurePosixPath:
        return self.root / destination

    async def exists(self, destination: PurePosixPath) -> bool:
        return await self.sftp.exists(self.resolve(destination))

    async def put(self, source: Path, destination: PurePosixPath) -> None:
        target = self.resolve(destination)
        staged = self.resolve(staging_path(destination))
        size = source.stat().st_size

        logger.info("Uploading %s (%d bytes) to %s", source, size, target)
        await self.sftp.makedirs(staged.parent, exist_ok=True)
        await self.sftp.makedirs(target.parent, exist_ok=True)

        started = monotonic()
        await self.sftp.put(source, staged)

        # A plain SFTP rename fails rather than overwriting, so a file that
        # appeared under us since assert_absent() is not silently destroyed.
        await self.sftp.rename(staged, target)
        await self.tidy_spool(staged.parent)

        elapsed = monotonic() - started
        logger.info(
            "Uploaded %s in %s (%.1f MB/s)",
            target,
            pretty_duration(elapsed),
            size / elapsed / 1e6 if elapsed else 0.0,
        )

    async def tidy_spool(self, directory: PurePosixPath) -> None:
        """Remove the staging directories the transfer just emptied.

        Best effort: a directory still holding something belongs to a
        concurrent job, and leaving it is harmless.
        """
        while directory != self.root:
            try:
                await self.sftp.rmdir(directory)
            except asyncssh.SFTPError:
                return
            directory = directory.parent


class SshArchiveStore(ArchiveStore):
    """Archive on another host, written over SSH."""

    def __init__(self, settings: SshArchiveSettings):
        reason = settings.unusable_reason()
        if reason is not None:
            raise ArchiveError(f"Cannot archive to {settings.host}: {reason}")

        self.settings = settings

    def connect_options(self) -> dict:
        settings = self.settings

        return {
            "host": settings.host,
            "port": settings.port,
            "username": settings.username,
            "connect_timeout": settings.connect_timeout,
            # Uploads are long and mostly one-way; notice a dead peer rather
            # than blocking on a transfer that will never finish.
            "keepalive_interval": 30,
            "client_keys": [settings.private_key_file],
            # Never pass None here: asyncssh reads that as "skip host key
            # verification". unusable_reason() guarantees this file exists.
            "known_hosts": str(settings.known_hosts_file),
        }

    @asynccontextmanager
    async def open(self) -> AsyncIterator[ArchiveSession]:
        logger.info("Connecting to archive host %s", self)
        async with (
            asyncssh.connect(**self.connect_options()) as connection,
            connection.start_sftp_client() as sftp,
        ):
            yield SshArchiveSession(sftp, self.settings.dir)

    def __str__(self) -> str:
        settings = self.settings
        return f"{settings.username}@{settings.host}:{settings.port}{settings.dir}"
