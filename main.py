#!/usr/bin/env python3
# encoding: utf-8
import argparse
import logging
import os
import shutil
import sys

import config
from fk_exceptions import AppError, SkippableError
from interactive import update_existing_file
from notify_runner import run_inotify, handle_file

FK_API = os.environ.get("FK_API", "https://frikanalen.no/api")
FK_TOKEN = os.environ.get("FK_TOKEN")
DIR = "/tmp"
TO_DIR = "/tank/media/"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


logging.basicConfig(level=logging.DEBUG)


def can_get_loudness():
    return shutil.which("bs1770gain")


def run(watch_dir, move_to_dir):
    for folder in os.listdir(watch_dir):
        if (
            folder.startswith(".")
            or not folder.isdigit()
            or not os.path.isdir(os.path.join(watch_dir, folder))
        ):
            continue
        try:
            handle_file(watch_dir, move_to_dir, int(folder))
        except SkippableError as e:
            logging.debug(e)
            logging.debug("Skipping %s" % folder)


def main():
    top_parser = argparse.ArgumentParser(
        description="Moving frikanalen videos and processing media files"
    )
    subparsers = top_parser.add_subparsers()

    common_args = argparse.ArgumentParser(add_help=False)
    common_args.add_argument(
        "--indir",
        help="""Folder where new `<id>/mediafile.mp4` are found
            (default: %(default)s)""",
        default=DIR,
    )
    common_args.add_argument(
        "--outdir",
        help="""Folder where `<id>/<media_folders>/<file>` are processed
            (default: %(default)s)""",
        default=TO_DIR,
    )
    common_args.add_argument(
        "--no-api", action="store_true", help="Do not call the API"
    )
    common_args.add_argument(
        "--api",
        help="Frikanalen API url (default: %(default)s, env: FK_API)",
        default=FK_API,
    )
    common_args.add_argument(
        "--token",
        help="Frikanalen API token to update database (env: FK_TOKEN)",
        default=FK_TOKEN,
    )

    process_p = subparsers.add_parser(
        "process",
        parents=[common_args],
        help="Reprocess media files (manual try-again mode)",
    )
    process_p.add_argument(
        "video_id", type=int, help="The ID of the video you want to update (ie. 626060)"
    )
    process_p.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite (rm) existing media files instead of skipping",
    )
    process_p.set_defaults(cmd=process_cmd)

    run_p = subparsers.add_parser(
        "run",
        parents=[common_args],
        help="Check files in in-folder and process files to out-folder",
    )
    run_p.set_defaults(cmd=run_cmd)

    daemon_p = subparsers.add_parser(
        "daemon",
        parents=[common_args],
        help="Listen for new folders in in-folder and process files to out-folder",
    )
    daemon_p.set_defaults(cmd=daemon_cmd)

    config.args = top_parser.parse_args()
    if "cmd" not in config.args:
        top_parser.print_help()
        sys.exit(1)
    try:
        config.args.cmd(config.args)
    except KeyboardInterrupt:
        pass
    except AppError as e:
        print()
        print(e)
        sys.exit(1)


def process_cmd(args):
    update_existing_file(args.video_id, args.outdir, force=args.force)


def daemon_cmd(args):
    run_inotify(args.indir, args.outdir)


def run_cmd(args):
    run(args.indir, args.outdir)


if __name__ == "__main__":
    main()
