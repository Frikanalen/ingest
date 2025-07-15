#!/usr/bin/env python3
# encoding: utf-8
import logging
import os
import shutil

from argparser import ArgumentParser
from fk_exceptions import SkippableError
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
    parser = ArgumentParser()
    parser.parse_and_execute()


def process_cmd(args):
    update_existing_file(args.video_id, args.outdir, force=args.force)


def daemon_cmd(args):
    run_inotify(args.indir, args.outdir)


def run_cmd(args):
    run(args.indir, args.outdir)


if __name__ == "__main__":
    main()
