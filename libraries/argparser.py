import argparse
import logging
import os
import sys

from .config import DIR, TO_DIR, FK_API, FK_TOKEN
from .fk_exceptions import AppError, SkippableError
from .interactive import update_existing_file
from .notify_runner import run_inotify, handle_file


def _create_common_arguments():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--indir",
        help="Folder where new `<id>/mediafile.mp4` are found (default: %(default)s)",
        default=DIR,
    )
    parser.add_argument(
        "--outdir",
        help="Folder where `<id>/<media_folders>/<file>` are processed (default: %(default)s)",
        default=TO_DIR,
    )
    parser.add_argument("--no-api", action="store_true", help="Do not call the API")
    parser.add_argument(
        "--api",
        help="Frikanalen API url (default: %(default)s, env: FK_API)",
        default=FK_API,
    )
    parser.add_argument(
        "--token",
        help="Frikanalen API token to update database (env: FK_TOKEN)",
        default=FK_TOKEN,
    )
    return parser


class ArgumentParser:
    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description="Moving frikanalen videos and processing media files"
        )
        self.common_args = _create_common_arguments()
        self.subparsers = self.parser.add_subparsers()

    def _add_process_parser(self):
        process_parser = self.subparsers.add_parser(
            "process",
            parents=[self.common_args],
            help="Reprocess media files (manual try-again mode)",
        )
        process_parser.add_argument(
            "video_id",
            type=int,
            help="The ID of the video you want to update (ie. 626060)",
        )
        process_parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Overwrite (rm) existing media files instead of skipping",
        )
        process_parser.set_defaults(cmd=process_cmd)

    def _add_run_parser(self):
        run_parser = self.subparsers.add_parser(
            "run",
            parents=[self.common_args],
            help="Check files in in-folder and process files to out-folder",
        )
        run_parser.set_defaults(cmd=run_cmd)

    def _add_daemon_parser(self):
        daemon_parser = self.subparsers.add_parser(
            "daemon",
            parents=[self.common_args],
            help="Listen for new folders in in-folder and process files to out-folder",
        )
        daemon_parser.set_defaults(cmd=daemon_cmd)

    def parse_and_execute(self):
        self._add_process_parser()
        self._add_run_parser()
        self._add_daemon_parser()

        args = self.parser.parse_args()
        if not hasattr(args, "cmd"):
            self.parser.print_help()
            sys.exit(1)

        try:
            args.cmd(args)
        except KeyboardInterrupt:
            pass
        except AppError as e:
            print(f"\n{str(e)}")
            sys.exit(1)


def process_cmd(args):
    update_existing_file(args.video_id, args.outdir, force=args.force)


def daemon_cmd(args):
    run_inotify(args.indir, args.outdir)


def run_cmd(args):
    run(args.indir, args.outdir)


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
