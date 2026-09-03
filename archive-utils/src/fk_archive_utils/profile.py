"""Which archive a command is allowed to touch.

The archive root is never an argument. It is looked up by name in
`/etc/fk-archive-utils/profiles.d/<name>.toml`, and the name is the first
argument -- which is what makes the sudoers rule able to pin it:

    ingest-staging ALL=(archive-manager-staging) NOPASSWD: /usr/bin/fk-archive staging *

sudo matches arguments literally up to the wildcard, so that rule lets the
staging ingest account run these tools against the staging archive and nothing
else. Had the root been a `--root` option instead, the same rule would have
had to trust the caller not to name production's, which is exactly the trust
this package exists to remove.

Profiles live on the storage host, owned by root, and are readable by
everyone: the forced-command wrapper runs as the unprivileged ingest account
and has to read the same file to know who to sudo to. There is nothing secret
in one.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from fk_archive_utils.errors import ProfileError

PROFILE_DIR = Path("/etc/fk-archive-utils/profiles.d")

#: Profile names are used to build a filename, so they get the same treatment
#: as anything else that becomes a path: a fixed, boring alphabet.
_NAME_ALPHABET = set("abcdefghijklmnopqrstuvwxyz0123456789-")

#: Owner-writable, readable by everyone else. The archive is exported
#: read-only over NFS to the playout hosts, so everything published has to be
#: readable by whoever the export maps them to.
DEFAULT_FILE_MODE = 0o644
DEFAULT_DIR_MODE = 0o755

#: Debian's out-of-process SFTP server. The wrapper execs this with -R so the
#: ingest account keeps the read access every backfill needs without keeping
#: any way to write.
DEFAULT_SFTP_SERVER = "/usr/lib/openssh/sftp-server"

_SETTINGS = frozenset({"root", "manager", "file_mode", "dir_mode", "sftp_server"})


@dataclass(frozen=True)
class Profile:
    name: str
    root: Path
    #: The account the mutating commands run as, and the account the forced
    #: command wrapper sudoes to. Recorded here rather than only in sudoers so
    #: the wrapper does not have to guess, and so a mismatch between the two
    #: shows up as sudo refusing rather than as the wrong archive being written.
    manager: str
    file_mode: int = DEFAULT_FILE_MODE
    dir_mode: int = DEFAULT_DIR_MODE
    sftp_server: str = DEFAULT_SFTP_SERVER


def _mode(data: dict, field: str, name: str, default: int) -> int:
    """Read an octal mode, written as a string.

    A string because TOML has no octal literal, and `0644` written as a TOML
    integer is 644 decimal -- which is a mode, just not the one anyone meant.
    Refusing the integer form outright is what keeps that from being a silent
    permissions bug on the archive.
    """
    if field not in data:
        return default
    raw = data[field]
    if not isinstance(raw, str):
        raise ProfileError(f'profile {name}: {field} must be an octal string such as "0644", got {raw!r}')
    try:
        value = int(raw, 8)
    except ValueError as e:
        raise ProfileError(f"profile {name}: {field} is not octal: {raw!r}") from e
    if not 0 <= value <= 0o7777:
        raise ProfileError(f"profile {name}: {field} is out of range: {raw!r}")
    return value


def load(name: str, *, profile_dir: Path = PROFILE_DIR) -> Profile:
    """Load the named profile, or explain why it cannot be used."""
    if not name or not set(name) <= _NAME_ALPHABET:
        raise ProfileError(f"{name!r} is not a profile name")

    path = profile_dir / f"{name}.toml"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as e:
        raise ProfileError(f"no archive profile named {name} in {profile_dir}") from e
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"profile {name} is not valid TOML: {e}") from e

    if unknown := set(data) - _SETTINGS:
        # Refused rather than ignored: a misspelt `file_modes` that quietly did
        # nothing would leave the archive with permissions nobody chose, and
        # nothing would ever say so.
        raise ProfileError(f"profile {name} has unknown settings: {', '.join(sorted(unknown))}")

    root = data.get("root")
    if not isinstance(root, str) or not root.startswith("/"):
        raise ProfileError(f"profile {name}: root must be an absolute path, got {root!r}")

    manager = data.get("manager")
    if not isinstance(manager, str) or not manager:
        raise ProfileError(f"profile {name}: manager must name the account these tools run as")

    sftp_server = data.get("sftp_server", DEFAULT_SFTP_SERVER)
    if not isinstance(sftp_server, str) or not sftp_server.startswith("/"):
        raise ProfileError(f"profile {name}: sftp_server must be an absolute path, got {sftp_server!r}")

    return Profile(
        name=name,
        root=Path(root),
        manager=manager,
        file_mode=_mode(data, "file_mode", name, DEFAULT_FILE_MODE),
        dir_mode=_mode(data, "dir_mode", name, DEFAULT_DIR_MODE),
        sftp_server=sftp_server,
    )
