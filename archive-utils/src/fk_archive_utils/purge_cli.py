"""`fk-archive-purge-trash` -- the one command that actually deletes.

Separate from `fk-archive` for one reason: so the sudoers rule that lets the
ingest engine publish, move and trash can end in a wildcard without also
letting it empty the trash. Nothing ingest can reach names this command.

It is meant for an operator, or for a systemd timer on the storage host
running as the archive account. `--dry-run` first is the habit worth having:
what comes out of it is the list of videos that are about to stop being
recoverable.
"""

import argparse
import os
import sys

from fk_archive_utils import operations
from fk_archive_utils.errors import ArchiveUtilsError
from fk_archive_utils.profile import PROFILE_DIR, load


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fk-archive-purge-trash",
        description="Permanently remove trash entries older than a given age.",
    )
    parser.add_argument("profile", help=f"archive profile to act on, as named in {PROFILE_DIR}")
    parser.add_argument(
        "--older-than",
        type=float,
        required=True,
        metavar="DAYS",
        help="only entries stamped at least this many days ago are removed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be removed and remove nothing",
    )
    return parser


def run(argv: list[str], stdout=None, profile_dir=PROFILE_DIR) -> int:
    args = build_parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout

    try:
        profile = load(args.profile, profile_dir=profile_dir)
        purged = operations.purge(profile, older_than_days=args.older_than, dry_run=args.dry_run)
    except ArchiveUtilsError as error:
        print(f"fk-archive-purge-trash: {error}", file=sys.stderr)
        return error.exit_code
    except OSError as error:
        print(f"fk-archive-purge-trash: {error}", file=sys.stderr)
        return ArchiveUtilsError.exit_code

    verb = "would remove" if args.dry_run else "removed"
    for candidate in purged:
        print(f"{verb} {candidate.name} (trashed {candidate.stamped_at:%Y-%m-%d %H:%M:%SZ})", file=stdout)
    plural = "entry" if len(purged) == 1 else "entries"
    print(f"{verb} {len(purged)} trash {plural} from {profile.name}", file=stdout)
    return 0


def main() -> None:
    os.umask(0o022)
    sys.exit(run(sys.argv[1:]))
