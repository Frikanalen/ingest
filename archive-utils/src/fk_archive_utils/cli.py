"""`fk-archive` -- the mutations the ingest engine is allowed to ask for.

One command with three verbs, rather than three commands, because the sudoers
rule that grants them is a single line and stays readable:

    ingest ALL=(archive-manager) NOPASSWD: /usr/bin/fk-archive prod *

Purging the trash is deliberately *not* one of the verbs. It is the one
operation that destroys anything, it ships as `fk-archive-purge-trash`, and
leaving it out of this command is what lets the rule above end in a wildcard
without also handing the ingest account a way to empty the trash.

Results go to stdout as one JSON object, so a caller reads a value rather than
parsing prose. Failures go to stderr as a sentence, and the exit code says
which kind of failure it was -- see `errors`.
"""

import argparse
import json
import os
import sys

from fk_archive_utils import operations
from fk_archive_utils.archive_path import parse_file_path, parse_removable_path
from fk_archive_utils.errors import ArchiveUtilsError
from fk_archive_utils.profile import PROFILE_DIR, Profile, load


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fk-archive",
        description="Perform one well-defined mutation on a Frikanalen media archive.",
    )
    parser.add_argument("profile", help=f"archive profile to act on, as named in {PROFILE_DIR}")
    verbs = parser.add_subparsers(dest="verb", required=True)

    publish = verbs.add_parser(
        "publish",
        help="read a file from stdin and publish it at <video-id>/<category>/<filename>",
    )
    publish.add_argument("destination")
    publish.add_argument(
        "--size",
        type=int,
        required=True,
        help="bytes the caller is about to send; a transfer of any other length is refused",
    )
    publish.add_argument(
        "--sha256",
        help="optional content hash, checked before anything is published",
    )

    move = verbs.add_parser("move", help="rename a file within one video's directory")
    move.add_argument("source")
    move.add_argument("destination")

    trash = verbs.add_parser("trash", help="move a video, or one directory in one, into .trash/")
    trash.add_argument("path")

    return parser


def run(argv: list[str], stdin=None, stdout=None, profile_dir=PROFILE_DIR) -> int:
    """Do what `argv` says, and return the exit code to leave with.

    Split out from main() so the whole command is testable without a
    subprocess: the tests drive this with a temporary profile directory and a
    BytesIO standing in for the SSH channel.
    """
    args = build_parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout

    try:
        profile = load(args.profile, profile_dir=profile_dir)
        result = _dispatch(args, profile, stdin if stdin is not None else sys.stdin.buffer)
    except ArchiveUtilsError as error:
        print(f"fk-archive: {error}", file=sys.stderr)
        return error.exit_code
    except OSError as error:
        # ENOSPC, EROFS, EDQUOT and friends. Reported rather than allowed to
        # become a traceback, because the traceback would go to the ingest
        # engine's log by way of an SSH channel and say less than this does.
        print(f"fk-archive: {args.verb} failed: {error}", file=sys.stderr)
        return ArchiveUtilsError.exit_code

    print(json.dumps({k: v for k, v in vars(result).items() if v is not None}), file=stdout)
    return 0


def _dispatch(args: argparse.Namespace, profile: Profile, stdin) -> operations.Result:
    match args.verb:
        case "publish":
            return operations.publish(
                profile,
                parse_file_path(args.destination, what="destination"),
                stdin,
                expected_size=args.size,
                expected_sha256=args.sha256,
            )
        case "move":
            return operations.move(
                profile,
                parse_file_path(args.source, what="source"),
                parse_file_path(args.destination, what="destination"),
            )
        case "trash":
            return operations.trash(profile, parse_removable_path(args.path, what="path"))
        case _:  # pragma: no cover - argparse rejects anything else first
            raise AssertionError(args.verb)


def main() -> None:
    # Whatever umask sudo inherited is not something the archive's permissions
    # should depend on. Every mode this package applies is set explicitly as
    # well; this is the belt to that pair of braces.
    os.umask(0o022)
    sys.exit(run(sys.argv[1:]))
