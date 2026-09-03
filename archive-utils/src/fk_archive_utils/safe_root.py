"""Traversing the archive without ever following a symlink.

The grammar in `archive_path` decides what a caller may name. This decides
what those names are allowed to resolve to, which is a different question: a
path made only of well-behaved components still escapes the archive if one of
those components is a symlink to somewhere else.

So nothing here ever hands a multi-component path to the kernel. The root is
opened once, and each component after it is opened relative to the directory
before it with O_NOFOLLOW, which fails outright on a symlink rather than
following it. Every mutation is then performed with `dir_fd` against the
directory that was reached that way, so there is no window between checking a
path and using it in which the path could become something else.

That matters more than it looks. The one account that can write into the
published tree is the one running these tools -- but `.trash` holds whatever
was last taken out of the archive, and a restore, a backup agent or an
operator's mistake can put a symlink there. Refusing to traverse one costs
nothing and removes the whole class.
"""

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from fk_archive_utils.errors import NotFound, UsageError

#: O_PATH would be enough for the directories we only traverse, but `os.listdir`
#: and `os.open(..., dir_fd=)` both want a descriptor that can be read from.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class SafeRoot:
    """The archive root, and the only way these tools reach anything under it."""

    def __init__(self, root: Path):
        self.root = root
        try:
            # The root itself is opened by path, and is the one place a symlink
            # is allowed: an archive root that is a symlink to the real dataset
            # is a perfectly ordinary way to run one, and it comes from the
            # profile on this host rather than from the caller.
            self._fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        except FileNotFoundError as e:
            raise UsageError(f"archive root {root} does not exist") from e
        except NotADirectoryError as e:
            raise UsageError(f"archive root {root} is not a directory") from e

    def close(self) -> None:
        os.close(self._fd)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def directory(self, parts: tuple[str, ...], *, create: bool = False, mode: int = 0o755) -> Iterator[int]:
        """Open the directory `parts` names, one component at a time.

        With `create`, missing components are made as we go. The mkdir is not
        guarded by a prior existence check on purpose: two publishes for the
        same video can be in flight at once, and EEXIST is the answer to "did
        someone else get here first", not a failure.
        """
        fd = os.dup(self._fd)
        try:
            for index, part in enumerate(parts):
                created = False
                if create:
                    try:
                        os.mkdir(part, mode, dir_fd=fd)
                    except FileExistsError:
                        pass
                    else:
                        created = True
                try:
                    nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError as e:
                    raise NotFound(f"{'/'.join(parts[: index + 1])} is not in the archive") from e
                except OSError as e:
                    # A symlink refused by O_NOFOLLOW reports as ELOOP on Linux
                    # and as ENOTDIR on macOS, where O_DIRECTORY is considered
                    # first. Both are refusals; asking what the component
                    # actually is decides only which sentence to print, and the
                    # answer is worth the syscall because "there is a symlink in
                    # your archive" is a very different thing to go and look at.
                    if e.errno not in (errno.ELOOP, errno.ENOTDIR):
                        raise
                    reached = "/".join(parts[: index + 1])
                    if _is_symlink(part, fd):
                        raise UsageError(f"{reached} is a symbolic link") from e
                    raise UsageError(f"{reached} is not a directory") from e
                os.close(fd)
                fd = nxt
                if created:
                    # mkdir applies the process umask, so a directory the
                    # playout export has to be able to read cannot be left to
                    # inherit whatever umask sudo happened to pass in. Applied
                    # to the descriptor rather than the name: Linux has no
                    # chmod that declines to follow a symlink, and by here the
                    # descriptor is provably the directory we just made.
                    os.fchmod(fd, mode)
            yield fd
        finally:
            os.close(fd)

    def lstat(self, parts: tuple[str, ...]) -> os.stat_result | None:
        """What `parts` is, without following a final symlink. None if absent."""
        with self.directory(parts[:-1]) as parent:
            try:
                return os.lstat(parts[-1], dir_fd=parent)
            except FileNotFoundError:
                return None

    def path_of(self, parts: tuple[str, ...]) -> Path:
        """The absolute path, for logging and for the one caller that needs it.

        Never fed back into an open: it is a string for humans, and for
        `shutil.rmtree`, which does its own descriptor-based walk.
        """
        return self.root.joinpath(*parts)


def _is_symlink(name: str, dir_fd: int) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode)
    except OSError:
        return False


def fsync_dir(fd: int) -> None:
    """Make a rename or a link survive the machine losing power.

    The archive is the only copy of a member's upload until someone backs it
    up, so a publish that returns success and is not there after a crash is
    the one outcome worth paying a round trip to avoid.
    """
    os.fsync(fd)
