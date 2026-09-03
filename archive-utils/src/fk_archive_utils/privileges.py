"""Becoming the account that owns the archive.

For the operator tools only. `fk-archive` is reached through sudo, which has
already made it the archive account by the time it runs; these are run by a
person, with plain `sudo`, and root is what can read that person's
`~/.frikanalen.yaml`.

Dropping afterwards is not tidiness. Every directory these tools create --
`<id>/original/`, `.trash/<stamp>/` -- would otherwise be owned by root, and
the account that has to write into them afterwards could not. A sweep that
left a root-owned `.trash/` behind would break the next one.
"""

import os
import pwd

from fk_archive_utils.profile import Profile


def drop_to_manager(profile: Profile) -> None:
    """Become the profile's manager account, permanently, or stay as we are.

    A no-op when this is not root: the operator has run it as the manager
    already, or as someone who will simply be refused by the filesystem, and
    either way there is nothing to drop.
    """
    if os.geteuid() != 0:
        return

    account = pwd.getpwnam(profile.manager)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
