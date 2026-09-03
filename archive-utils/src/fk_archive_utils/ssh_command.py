"""`fk-archive-ssh` -- the only thing the ingest key can run.

Installed as the forced command on the ingest account's authorised key:

    restrict,command="/usr/bin/fk-archive-ssh prod" ssh-ed25519 AAAA...

sshd runs this instead of whatever the client asked for, and puts what the
client asked for in SSH_ORIGINAL_COMMAND. Everything it will dispatch to is
here, and it is two things: a read-only SFTP server, and `fk-archive` by way
of sudo.

This is not the security boundary -- it runs as the unprivileged ingest
account, and sudoers is what actually decides which command may be run as the
archive account. What it removes is the step before that: without it, an
account that can exec anything at all is an account a stolen key can run
arbitrary code as, on the host holding every video Frikanalen has.

The SFTP half is why the split exists. The ingest engine still reads from the
archive constantly -- a backfill fetches the original before it can rebuild
anything -- and `-R` gives it every one of those reads with no way to write.
The writes go through the sudo half instead, where each one is a named
mutation rather than an open file descriptor.
"""

import os
import shlex
import sys
from pathlib import Path

from fk_archive_utils.errors import ArchiveUtilsError, ProfileError, UsageError
from fk_archive_utils.profile import Profile, load

#: Where the package installs the mutation tool, and what the sudoers rule
#: names. Absolute and not configurable: a wrapper that resolved this through
#: PATH would let whatever PATH the login shell exported choose the binary.
FK_ARCHIVE = "/usr/bin/fk-archive"

#: Likewise absolute. This is the step that crosses from the ingest account to
#: the archive account, and it should not be reachable through a PATH entry
#: anyone but root can write.
SUDO = "/usr/bin/sudo"

#: What sshd's `Subsystem sftp` line can be set to. The client does not choose
#: this -- sshd substitutes its own configured command for a subsystem request
#: -- so this is a check that the request really was for SFTP rather than a
#: gate on anything the caller controls.
SFTP_SERVERS = frozenset({"sftp-server", "internal-sftp", "sftp"})


def resolve(original_command: str, profile: Profile) -> list[str]:
    """The argv to exec for `original_command`, or a refusal.

    Separated from the exec so the dispatch table is testable as a function
    from a string to a command line, which is the whole of what it decides.
    """
    try:
        tokens = shlex.split(original_command)
    except ValueError as e:
        raise UsageError(f"could not parse the requested command: {e}") from e

    if not tokens:
        raise UsageError("this account runs archive commands and nothing else; there is no shell here")

    requested = Path(tokens[0]).name

    if requested in SFTP_SERVERS:
        # -R is read-only: the server refuses open-for-write, rename, remove,
        # mkdir and setstat outright, so this half of the account cannot alter
        # the archive however the client asks.
        return [profile.sftp_server, "-R"]

    if requested == "fk-archive":
        # The profile is supplied here rather than taken from the request, so
        # the staging key cannot name production's archive whatever it sends.
        return [SUDO, "-n", "-u", profile.manager, "--", FK_ARCHIVE, profile.name, *tokens[1:]]

    raise UsageError(f"{requested!r} is not something this key may run")


def main() -> None:
    if len(sys.argv) != 2:
        print("fk-archive-ssh: usage: fk-archive-ssh <profile>, as a forced command", file=sys.stderr)
        sys.exit(UsageError.exit_code)

    try:
        profile = load(sys.argv[1])
        argv = resolve(os.environ.get("SSH_ORIGINAL_COMMAND", ""), profile)
    except ArchiveUtilsError as error:
        print(f"fk-archive-ssh: {error}", file=sys.stderr)
        sys.exit(error.exit_code)

    try:
        os.execv(argv[0], argv)
    except OSError as error:
        # Everything this dispatches to is named by an absolute path, so a
        # failure here is a host that is missing a package rather than a
        # request that was wrong -- which is why it reports as a configuration
        # fault and not as a usage error.
        print(f"fk-archive-ssh: cannot run {argv[0]}: {error}", file=sys.stderr)
        sys.exit(ProfileError.exit_code)
