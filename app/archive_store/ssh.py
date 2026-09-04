import asyncio
import getpass
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path, PurePosixPath
from time import monotonic

import asyncssh

from app.archive_store import fk_archive
from app.archive_store.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveSession,
    ArchiveStore,
)
from app.util.pretty_duration import pretty_duration
from app.util.settings import SshArchiveSettings

logger = getLogger(__name__)

# Stands in for the account running the process when the system cannot name it.
FALLBACK_LOCAL_USERNAME = "ingest"

#: How much of a file is read and handed to the channel at a time. Large
#: enough that a 20 GB original is not a hundred thousand round trips through
#: a thread, small enough to be nothing next to the pod's memory limit.
CHUNK_BYTES = 1024 * 1024


def ensure_local_username() -> None:
    """Make sure the account running this process has a resolvable name.

    asyncssh looks up the local username to expand ssh_config, before it
    considers any of the options passed to it, even though nothing here uses
    it: the account we log in to the archive as is configured separately. In a
    container running as a bare uid there is no passwd entry to find, so that
    lookup raises and every upload dies with "Unknown local username".

    The image gives the uid a real account, which is the proper fix, but the
    deployment is free to run as some other uid and the failure would not
    appear until the first upload. Naming ourselves is cheap insurance.
    """
    try:
        getpass.getuser()
    except KeyError:
        logger.warning(
            "The local account has no name; calling ourselves %s so SSH can proceed",
            FALLBACK_LOCAL_USERNAME,
        )
        os.environ["USER"] = FALLBACK_LOCAL_USERNAME


class SshArchiveSession(ArchiveSession):
    """The archive on another host, read over SFTP and mutated by command.

    The two halves reach the storage host down the same connection and arrive
    as different accounts. Reads go to an SFTP server the far end runs
    read-only, so this session has no way to write through it however it asks.
    Writes are requests to `fk-archive`, which sudo runs as the account that
    owns the media -- so a mutation is a named operation the far end agreed to
    perform, rather than a file descriptor this process holds.

    That split is why the session keeps the connection as well as the SFTP
    client: `put` and `trash` need a channel of their own, and the connection
    is the thing that opens one.
    """

    def __init__(self, connection: asyncssh.SSHClientConnection, sftp: asyncssh.SFTPClient, root: PurePosixPath):
        self.connection = connection
        self.sftp = sftp
        self.root = root

    def resolve(self, destination: PurePosixPath) -> PurePosixPath:
        """Where `destination` is on the storage host, for reading.

        Only the read half needs this. `fk-archive` looks the archive root up
        by profile and never accepts one as an argument, so a mutation names a
        path relative to a root this process does not get to choose -- which
        means `FK_ARCHIVE_DIR` and the profile's `root` on the storage host
        have to be the same directory. They are the two ends of one archive,
        and pointing them at different ones publishes files this session then
        cannot find.
        """
        return self.root / destination

    async def exists(self, destination: PurePosixPath) -> bool:
        return await self.sftp.exists(self.resolve(destination))

    async def put(self, source: Path, destination: PurePosixPath) -> None:
        """Publish `source` by streaming it to `fk-archive publish`.

        The bytes go up the command's standard input rather than into a spool
        this process fills, because a spool the ingest account can write to is
        a spool whose files it owns -- and a rename does not change that, so
        every file ever published would stay writable by us, which is the whole
        of the permission this arrangement exists to remove.

        The size is measured here and promised in the request. The far end
        refuses a stream of any other length, which is the only thing that can
        tell a complete transfer from a connection that dropped: a truncated
        stream ends exactly like a whole one.
        """
        size = source.stat().st_size
        logger.info("Publishing %s (%d bytes) as %s", source, size, destination)

        started = monotonic()
        await self._run(fk_archive.publish(destination, size=size), stdin=source)
        elapsed = monotonic() - started

        logger.info(
            "Published %s in %s (%.1f MB/s)",
            destination,
            pretty_duration(elapsed),
            size / elapsed / 1e6 if elapsed else 0.0,
        )

    async def trash(self, path: PurePosixPath) -> PurePosixPath:
        logger.info("Trashing %s", path)
        result = await self._run(fk_archive.trash(path))

        destination = result.get("destination")
        if destination is None:
            # Where it went is the only reason a caller logs the result at all:
            # putting it back is a rename from there, and a removal nobody can
            # name the far side of is one nobody can undo.
            raise ArchiveError(f"the archive trashed {path} but did not say where it went: {result}")
        return PurePosixPath(destination)

    async def list_dir(self, destination: PurePosixPath) -> list[ArchiveEntry]:
        resolved = self.resolve(destination)

        # Asked before reading rather than caught after: asyncssh raises a
        # different SFTPError depending on what the server decided the problem
        # was, and "no such directory" is not a problem here anyway.
        if not await self.sftp.isdir(resolved):
            return []

        entries = [
            ArchiveEntry(
                path=destination / entry.filename,
                is_dir=stat.S_ISDIR(entry.attrs.permissions or 0),
            )
            for entry in await self.sftp.readdir(resolved)
            if entry.filename not in (".", "..")
        ]
        return sorted(entries, key=lambda entry: entry.path)

    async def get(self, source: PurePosixPath, destination: Path) -> None:
        origin = self.resolve(source)
        staged = destination.with_name(destination.name + ".part")

        logger.info("Downloading %s to %s", origin, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        started = monotonic()
        try:
            await self.sftp.get(origin, staged)
        except asyncssh.SFTPError as e:
            # A missing original is a condition callers act on -- it is what a
            # videofile row pointing at nothing looks like from here -- so it
            # arrives as the same exception the local archive raises, rather
            # than as an asyncssh type only one backend can produce.
            if not await self.sftp.exists(origin):
                raise FileNotFoundError(f"{source} is not in the archive") from e
            raise
        staged.replace(destination)

        elapsed = monotonic() - started
        size = destination.stat().st_size
        logger.info(
            "Downloaded %s in %s (%.1f MB/s)",
            origin,
            pretty_duration(elapsed),
            size / elapsed / 1e6 if elapsed else 0.0,
        )

    async def _run(self, command: str, *, stdin: Path | None = None) -> dict:
        """Ask the storage host to do one thing, and read what it says back.

        The channel is opened in binary, because half of what goes up it is a
        video file. What comes back is small either way: one line of JSON, or a
        sentence and an exit code.
        """
        try:
            async with self.connection.create_process(command, encoding=None) as process:
                if stdin is not None:
                    await self._send(stdin, process.stdin)
                else:
                    process.stdin.write_eof()
                completed = await process.wait()
        except asyncssh.ChannelOpenError as e:
            raise ArchiveError(f"the archive host would not run {command}: {e}") from e

        return fk_archive.interpret(command, completed.returncode, completed.stdout, completed.stderr)

    @staticmethod
    async def _send(source: Path, sink: asyncssh.SSHWriter) -> None:
        """Stream `source` into the command's standard input.

        Read in a thread, because the file is on local disk and can be
        gigabytes; written with a drain between chunks, so the channel's flow
        control rather than this process's memory decides how much is in
        flight.

        A broken pipe here is not the failure to report. The far end refuses a
        malformed destination in its argument parser, before it has read a byte
        of the file, so the first thing this notices about that refusal is that
        nobody is reading any more. What went wrong is the exit code, which is
        still to come -- so this stops sending and lets wait() say.
        """
        try:
            with source.open("rb") as handle:
                while chunk := await asyncio.to_thread(handle.read, CHUNK_BYTES):
                    sink.write(chunk)
                    await sink.drain()
            sink.write_eof()
        except (BrokenPipeError, ConnectionResetError, asyncssh.Error):
            logger.debug("The archive stopped reading %s before it was sent in full", source)


class SshArchiveStore(ArchiveStore):
    """Archive on another host, read over SSH and mutated through it."""

    def __init__(self, settings: SshArchiveSettings):
        reason = settings.unusable_reason()
        if reason is not None:
            raise ArchiveError(f"Cannot archive to {settings.host}: {reason}")

        # At startup rather than per-connection, so a container that cannot
        # name its own user is sorted out before the first upload rather than
        # during it.
        ensure_local_username()

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
            yield SshArchiveSession(connection, sftp, self.settings.dir)

    def __str__(self) -> str:
        settings = self.settings
        return f"{settings.username}@{settings.host}:{settings.port}{settings.dir}"
