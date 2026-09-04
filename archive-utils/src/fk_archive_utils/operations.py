"""The five things anything is allowed to do to the archive.

Publish a file, move a file inside one video, trash a directory, permanently
delete one explicitly allowed regenerable variant, and purge what has been in
the trash long enough. That is the entire set, boiled down until each operation
has the narrowest subject its caller needs.

Three of them are reachable over SSH. `fk-archive` offers publish, trash and
delete-variant; move and purge are operator tools with their own entry points,
because neither is something a running ingest engine has any reason to ask for.

Two properties are load-bearing and are worth stating once here rather than
per operation:

**Nothing is ever overwritten.** Both publish and move link the new name into
place and unlink the old one, because `link(2)` fails with EEXIST rather than
replacing -- `rename(2)` would replace silently, and there is no portable
no-clobber rename to reach for. The archive is exported read-only to the
playout hosts, and a file swapped under a reader is worse than a refusal.

**Nothing the ingest account can reach destroys anything that cannot be rebuilt
from the original.** Trash is a rename into `.trash/<stamp>/`; purge is kept in
a separate operator command; and the one destructive SSH verb is restricted to
an explicit allowlist of cheap, regenerable derivatives.
"""

import os
import shutil
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fk_archive_utils.archive_path import SPOOL_DIR, TRASH_DIR, ArchivePath
from fk_archive_utils.errors import AlreadyExists, NotFound, TransferError, UsageError
from fk_archive_utils.profile import Profile
from fk_archive_utils.safe_root import SafeRoot, fsync_dir

#: Big enough that a 20 GB original is not a hundred thousand syscalls, small
#: enough that the refusal for a size mismatch does not wait on a huge read.
CHUNK_BYTES = 4 * 1024 * 1024

#: How a trash entry is stamped, and what `purge` parses back out of it.
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
STAMP_LENGTH = len(datetime(2000, 1, 1, tzinfo=UTC).strftime(STAMP_FORMAT))

#: Variants `delete-variant` may remove. An allowlist rather than a list of
#: protected names, because the failure mode of a denylist is that a variant
#: added later is deletable by default and nobody revisits this file. The
#: property wanted is "regenerable from the original, and cheap enough that
#: destroying it is not an incident" -- which today is exactly one thing.
DELETABLE_VARIANTS = frozenset({"dash_preview"})


@dataclass(frozen=True)
class Result:
    """What an operation did, in a form the caller can read off stdout."""

    operation: str
    path: str
    #: Where the thing ended up, when that is not the path that was asked for:
    #: the trash location, which the ingest engine logs so a mistake can be
    #: undone by hand.
    destination: str | None = None
    bytes_written: int | None = None
    deleted: bool | None = None
    files_removed: int | None = None
    bytes_removed: int | None = None


def publish(
    profile: Profile,
    destination: ArchivePath,
    stream,
    *,
    expected_size: int,
) -> Result:
    """Take a file on stdin and publish it at `destination`.

    The bytes arrive on the command's standard input rather than being fetched
    from a spool directory the caller filled in beforehand, and that is the
    decision the whole package turns on. A spool the ingest account can write
    to is a spool whose files the ingest account *owns* -- and a rename does
    not change that, so every file it ever published would stay writable by
    it, which is the general write permission this exists to remove. Streaming
    through here means the archive account creates the file itself and the
    ingest account needs no write access to the storage host at all.

    The staging is still real, just on this side of the fence: bytes land in
    `.spool/`, are checked against the size the caller promised, and are only
    linked into the published tree once they are all there. An interrupted
    transfer leaves nothing a reader can see, which is what the read-only NFS
    export to the playout hosts requires.

    `expected_size` is not optional, because it is the only thing that can tell
    a complete transfer apart from a connection that dropped: a truncated
    stream ends exactly like a whole one. It is also the only check made on the
    content, and deliberately: SSH already carries its own integrity check, and
    a digest the sender computes from the same bytes it then sends agrees with
    itself whatever went wrong before it was read.
    """
    if expected_size < 0:
        raise UsageError("--size must not be negative")

    with (
        SafeRoot(profile.root) as archive,
        archive.directory((SPOOL_DIR,), create=True, mode=0o700) as spool,
    ):
        staged = f"incoming-{os.getpid()}-{os.urandom(8).hex()}"
        try:
            written = _receive(stream, staged, spool, profile)

            if written != expected_size:
                raise TransferError(f"expected {expected_size} bytes, received {written}")

            with archive.directory(destination.parent, create=True, mode=profile.dir_mode) as parent:
                try:
                    os.link(staged, destination.name, src_dir_fd=spool, dst_dir_fd=parent, follow_symlinks=False)
                except FileExistsError as e:
                    raise AlreadyExists(f"{destination} is already in the archive") from e
                fsync_dir(parent)
        finally:
            # Removed whichever way this went. On success the published
            # name is the same inode and the spool copy is redundant; on
            # failure the caller still has the file it was sending, so
            # keeping a partial here would only fill a directory nobody
            # ever looks in.
            with suppress(FileNotFoundError):
                os.unlink(staged, dir_fd=spool)

    return Result("publish", str(destination), bytes_written=written)


def _receive(stream, staged: str, spool: int, profile: Profile) -> int:
    """Write the stream into the spool, and say how much of it arrived.

    O_EXCL against a name nothing else can predict, so this cannot be pointed
    at an existing file, and O_NOFOLLOW so it cannot be pointed through a
    symlink either.
    """
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, profile.file_mode, dir_fd=spool)
    written = 0
    try:
        with open(fd, "wb", closefd=False) as sink:
            while chunk := stream.read(CHUNK_BYTES):
                sink.write(chunk)
                written += len(chunk)
        # The mode passed to open() is masked by the process umask, which sudo
        # inherits from whatever invoked it. Set it outright so the playout
        # export does not depend on the caller's environment.
        os.fchmod(fd, profile.file_mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    return written


def move(profile: Profile, source: ArchivePath, destination: ArchivePath) -> Result:
    """Rename one file to another name inside the same video's directory.

    Exists for exactly one caller: `migrate_broadcast`. Deliberately not a verb
    of `fk-archive`, so no SSH session can reach it -- a one-shot migration is
    not a reason to give a long-running service a standing permission to rename
    archived media. When the last `broadcast/` directory is gone, this
    function, that module and its entry point go together.

    Scoped to one video id because that is the only shape the migration has,
    and because a move that could cross between videos is a way to detach a
    file from the row that names it -- which is the incident this package is
    supposed to make impossible rather than merely unlikely.
    """
    if source.video_id != destination.video_id:
        raise UsageError(f"a move stays inside one video: {source.video_id} != {destination.video_id}")
    if source.parts == destination.parts:
        raise UsageError(f"{source} and {destination} are the same path")

    with SafeRoot(profile.root) as archive:
        info = archive.lstat(source.parts)
        if info is None:
            raise NotFound(f"{source} is not in the archive")
        if not _is_regular_file(info):
            # Directories cannot be hardlinked, so the no-clobber trick below
            # would not work on one -- but the real reason is that nothing asks
            # to move a directory, and a mutation nobody needs is a mutation
            # this package should not offer.
            raise UsageError(f"{source} is not a regular file")

        with (
            archive.directory(source.parent) as origin,
            archive.directory(destination.parent, create=True, mode=profile.dir_mode) as target,
        ):
            try:
                os.link(source.name, destination.name, src_dir_fd=origin, dst_dir_fd=target, follow_symlinks=False)
            except FileExistsError as e:
                raise AlreadyExists(f"{destination} is already in the archive") from e
            os.unlink(source.name, dir_fd=origin)
            fsync_dir(target)
            fsync_dir(origin)

    return Result("move", str(source), destination=str(destination))


def trash(profile: Profile, path: ArchivePath, *, now: datetime | None = None) -> Result:
    """Take `path` out of the published tree, and say where it went.

    A rename, never a delete. What is trashed is either a whole video whose
    row the catalogue no longer has, or one directory inside a video that an
    upload has superseded or a rebuild is about to replace -- and both of those
    decisions are made from a snapshot that may be minutes old by the time it
    is acted on. Trashing costs a rename back when the decision was wrong.

    Each call gets its own stamp directory, created exclusively. That is not
    only tidiness: it is what makes the rename below safe without a no-clobber
    rename, since nothing else can already be inside a directory this process
    just created.
    """
    stamp_base = (now or datetime.now(UTC)).strftime(STAMP_FORMAT)

    with SafeRoot(profile.root) as archive:
        if archive.lstat(path.parts) is None:
            raise NotFound(f"{path} is not in the archive")

        with archive.directory((TRASH_DIR,), create=True, mode=profile.dir_mode) as trash_dir:
            stamp = _unique_stamp(stamp_base, trash_dir, profile.dir_mode)
            destination = ArchivePath((TRASH_DIR, stamp, *path.parts))

            with (
                archive.directory(path.parent) as origin,
                archive.directory(destination.parent, create=True, mode=profile.dir_mode) as target,
            ):
                os.rename(path.name, destination.name, src_dir_fd=origin, dst_dir_fd=target)
                fsync_dir(target)
                fsync_dir(origin)

    return Result("trash", str(path), destination=str(destination))


def _unique_stamp(base: str, trash_dir: int, mode: int) -> str:
    """A stamp directory this call owns outright.

    Two removals in the same second are ordinary -- superseding an upload
    trashes every format directory in a row -- so the second one takes
    `<stamp>.1`. `purge` reads the timestamp off the front of the name, so a
    suffix costs it nothing.
    """
    for suffix in range(1000):
        candidate = base if suffix == 0 else f"{base}.{suffix}"
        try:
            os.mkdir(candidate, mode, dir_fd=trash_dir)
        except FileExistsError:
            continue
        return candidate
    raise UsageError(f"could not find an unused trash directory for {base}")


def _is_regular_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode)


def delete_variant(profile: Profile, path: ArchivePath) -> Result:
    """Permanently remove one explicitly deletable derivative variant.

    This is narrower than trash: its subject is always exactly one variant of
    one video, and the allowlist contains only media that is both regenerable
    from the original and cheap enough to destroy routinely. Keeping previews
    out of `.trash` preserves that directory as a signal that an operator may
    need to inspect something before purge destroys it.

    `rmtree` is used for the same reason as in `purge`: CPython uses a
    descriptor-based, symlink-safe walk on Linux. Unlike purge, this operation
    is reachable by the ingest account, so its initial name is resolved
    relative to the already-validated parent descriptor as well.
    """
    if path.name not in DELETABLE_VARIANTS:
        raise UsageError(f"variant is not deletable: {path.name!r}")

    with SafeRoot(profile.root) as archive:
        try:
            info = archive.lstat(path.parts)
        except NotFound:
            # A missing video and a missing variant are the same requested end
            # state. In particular, neither is a failure worth retrying.
            info = None
        if info is None:
            return Result("delete-variant", str(path), deleted=False)
        if not stat.S_ISDIR(info.st_mode):
            raise UsageError(f"{path} is not a directory")

        with archive.directory(path.parent) as parent:
            target = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                files_removed, bytes_removed = _tree_usage(target)
            finally:
                os.close(target)
            shutil.rmtree(path.name, dir_fd=parent)
            fsync_dir(parent)

    return Result(
        "delete-variant",
        str(path),
        deleted=True,
        files_removed=files_removed,
        bytes_removed=bytes_removed,
    )


def _tree_usage(directory: int) -> tuple[int, int]:
    """Count non-directories through an open directory, without following links.

    Symlinks count as files, and their byte count is the length of the link text
    reported by lstat rather than the size of anything they point to.
    """
    files = 0
    size = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                nested = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory,
                )
                try:
                    nested_files, nested_size = _tree_usage(nested)
                finally:
                    os.close(nested)
                files += nested_files
                size += nested_size
            else:
                files += 1
                size += info.st_size
    return files, size


@dataclass(frozen=True)
class PurgeCandidate:
    name: str
    stamped_at: datetime
    path: Path


def purge(
    profile: Profile,
    *,
    older_than_days: float,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[PurgeCandidate]:
    """Delete trash entries stamped longer than `older_than_days` ago.

    The only operation that destroys archived material which may not be
    regenerable from the original, which is why it ships as its own command:
    the ingest account's sudoers rule names the other tool, so nothing the
    ingest engine can reach has a way to call this.

    An entry whose name does not begin with a timestamp is left alone and
    reported. Something put it there that was not `trash`, and guessing its
    age in order to delete it is not a guess worth making.
    """
    if older_than_days < 0:
        raise UsageError("--older-than must not be negative")

    cutoff = (now or datetime.now(UTC)).timestamp() - older_than_days * 86400

    purged: list[PurgeCandidate] = []

    with SafeRoot(profile.root) as archive:
        try:
            with archive.directory((TRASH_DIR,)) as trash_dir:
                names = sorted(os.listdir(trash_dir))
        except NotFound:
            return purged

        for name in names:
            stamped_at = _stamped_at(name)
            if stamped_at is None or stamped_at.timestamp() >= cutoff:
                continue
            candidate = PurgeCandidate(name, stamped_at, archive.path_of((TRASH_DIR, name)))
            if not dry_run:
                # rmtree rather than a descriptor walk of our own: CPython's
                # is already symlink-safe on Linux, and the directory it is
                # pointed at is one only this package's own account can write.
                shutil.rmtree(candidate.path)
            purged.append(candidate)

    return purged


def _stamped_at(name: str) -> datetime | None:
    """The time a trash entry was stamped, or None if it was not stamped by us."""
    try:
        return datetime.strptime(name[:STAMP_LENGTH], STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
