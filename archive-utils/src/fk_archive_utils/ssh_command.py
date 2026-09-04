"""`fk-archive-ssh` -- the only thing the ingest key can run.

Installed as the forced command on the ingest account's authorised key:

    restrict,command="/usr/bin/fk-archive-ssh prod" ssh-ed25519 AAAA...

sshd runs this instead of whatever the client asked for, and puts what the
client asked for in SSH_ORIGINAL_COMMAND. Everything it will dispatch to is
here, and it is one thing: `fk-archive` by way of sudo.

One thing, because reads do not come this way. The archive is exported
read-only over NFS and the engine mounts it, so a backfill fetching an
original never opens an SSH channel at all -- which leaves this key with no
reason to reach a file descriptor on the storage host, and no way to.

This is not the security boundary -- it runs as the unprivileged ingest
account, and sudoers is what actually decides which command may be run as the
archive account. What it removes is the step before that: without it, an
account that can exec anything at all is an account a stolen key can run
arbitrary code as, on the host holding every video Frikanalen has.

What it adds is the profile. `fk-archive` takes it as its first argument and
this supplies it, so a key issued for staging cannot name production's archive
however it is invoked.
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


def resolve(original_command: str, profile: Profile) -> list[str]:
    """The argv to exec for `original_command`, or a refusal.

    Separated from the exec so the dispatch is testable as a function from a
    string to a command line, which is the whole of what it decides.
    """
    try:
        tokens = shlex.split(original_command)
    except ValueError as e:
        raise UsageError(f"could not parse the requested command: {e}") from e

    if not tokens:
        raise UsageError("this account runs archive commands and nothing else; there is no shell here")

    requested = Path(tokens[0]).name

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
