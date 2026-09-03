"""What the tools a person runs by hand have in common.

Three of the commands here are not reachable over SSH, and two of those read
the catalogue as well as the archive. They share a way of being invoked, and
it is worth having in one place because each half of it is a decision:

* the fk-cli environment defaults to the archive profile's own name, so
  pairing the production archive with whichever catalogue an operator last
  selected takes an explicit `--environment` rather than an oversight;
* the credentials are read before privileges are dropped, because
  `~/.frikanalen.yaml` belongs to the person running the command;
* privileges are then dropped for good, because every directory these create
  -- `<id>/original/`, `.trash/<stamp>/` -- has to be owned by the account
  that will write into it afterwards. One left owned by root breaks the next
  run.

Which is why they are run with plain `sudo`: root is what can read that file,
and nothing after the read needs it.
"""

import argparse
import os
import pwd
from dataclasses import dataclass

from fk_archive_utils.catalogue import Catalogue, Credentials, load_credentials
from fk_archive_utils.profile import PROFILE_DIR, Profile, load


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The profile, and how to reach the catalogue it belongs to."""
    parser.add_argument("profile", help=f"archive profile to act on, as named in {PROFILE_DIR}")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="carry it out; without it nothing is changed and the plan is printed",
    )
    parser.add_argument(
        "--environment",
        help="fk-cli environment to authenticate as. Defaults to the profile name, which is what "
        "keeps the production archive from being paired with staging's catalogue.",
    )
    parser.add_argument("--config", help="fk-cli configuration file (default ~/.frikanalen.yaml)")
    parser.add_argument("--api-url", help="django-api base URL, if the configuration file has none")
    return parser


@dataclass(frozen=True)
class Context:
    profile: Profile
    catalogue: Catalogue
    credentials: Credentials


def prepare(args: argparse.Namespace, profile_dir=PROFILE_DIR) -> Context:
    """Resolve the profile and the catalogue, then stop being root.

    The catalogue is handed `--apply` too, so "without it nothing is changed"
    holds at the far end as well as in the archive -- a property of this
    plumbing rather than of each tool remembering it.
    """
    profile = load(args.profile, profile_dir=profile_dir)
    environment = args.environment or profile.name
    credentials = load_credentials(environment, config_path=args.config, api_url=args.api_url)
    _drop_to_manager(profile)
    return Context(
        profile=profile,
        catalogue=Catalogue(credentials, dry_run=not args.apply),
        credentials=credentials,
    )


def _drop_to_manager(profile: Profile) -> None:
    """Become the profile's manager account, permanently, or stay as we are.

    A no-op when this is not root: the operator has run it as the manager
    already, or as someone the filesystem will simply refuse, and either way
    there is nothing to drop.
    """
    if os.geteuid() != 0:
        return

    account = pwd.getpwnam(profile.manager)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
