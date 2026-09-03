"""`fk-archive-migrate-broadcast` -- put every video's source under original/.

`broadcast/` is what the system before this one called a video's source file.
Nothing has written one for years, and everything since expects the source
under `original/`. This walks the archive once and settles that, and then it
should be deleted -- along with `catalogue`, `operations.move`, this entry
point and the package's dependency on python3-yaml. A migration that stays in
the code after it has finished migrating reads like a rule about how the
archive works, which it is not.

It used to be a chore in the ingest engine's backfill, which meant the engine
needed a standing permission to rename files in the archive in order to run a
migration that happens once. It does not any more: this is not a verb of
`fk-archive`, so no SSH session can reach it, and the permission it would have
required does not exist.

The decision it makes per video is the chore's, unchanged:

* not in the catalogue -- leave it; the backfill's gc takes the whole video
* no files in `broadcast/` -- nothing to do
* `original/` already holds the source -- the broadcast copy is redundant
  weight, so trash it and drop the rows that named it
* `broadcast/` holds media nothing claims -- say so and leave it; moving it
  would be guessing that it is this video's source
* otherwise -- move each file to `original/`, retag the rows, trash the
  emptied directory

Order matters in the last two. Trash before unregister, so a failure between
them leaves media removed but still recorded rather than the reverse; move and
retag before trashing the directory, so nothing is ever recorded at a path
that does not yet hold it.
"""

import argparse
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from fk_archive_utils import operations
from fk_archive_utils.archive_path import parse_file_path, parse_removable_path
from fk_archive_utils.catalogue import Catalogue, load_credentials
from fk_archive_utils.errors import ArchiveUtilsError, UsageError
from fk_archive_utils.privileges import drop_to_manager
from fk_archive_utils.profile import PROFILE_DIR, Profile, load
from fk_archive_utils.safe_root import SafeRoot

BROADCAST_DIR = "broadcast"
ORIGINAL_DIR = "original"

#: The variant a row carries while it still names a file under broadcast/,
#: and the one it should carry afterwards.
BROADCAST_VARIANT = "broadcast"
ORIGINAL_VARIANT = "original"


@dataclass
class VideoReport:
    video_id: str
    #: What was decided, in the words this prints. One line per video is what
    #: makes a run over the whole catalogue reviewable at all.
    outcome: str
    moved: list[str] = field(default_factory=list)
    retagged: list[int] = field(default_factory=list)
    unregistered: list[int] = field(default_factory=list)
    trashed: str | None = None
    failed: str | None = None

    def describe(self) -> str:
        line = f"video {self.video_id}: {self.outcome}"
        for path in self.moved:
            line += f"\n    moved {path} to {ORIGINAL_DIR}/"
        for file_id in self.retagged:
            line += f"\n    retagged videofile {file_id} as {ORIGINAL_VARIANT}"
        for file_id in self.unregistered:
            line += f"\n    unregistered videofile {file_id}"
        if self.trashed:
            line += f"\n    trashed {BROADCAST_DIR}/ to {self.trashed}"
        if self.failed:
            line += f"\n    FAILED: {self.failed}"
        return line


def find_candidates(profile: Profile) -> list[str]:
    """Every video with a `broadcast/` directory, in catalogue order.

    A full listing of the archive root, which on a catalogue this size is one
    readdir and a stat apiece. Cheap enough that there is no reason to make
    the operator supply a list.
    """
    candidates = []
    with SafeRoot(profile.root) as archive, archive.directory(()) as root:
        for name in sorted(os.listdir(root), key=_sort_key):
            if not name.isdigit():
                continue
            if archive.lstat((name, BROADCAST_DIR)) is not None:
                candidates.append(name)
    return candidates


def _sort_key(name: str) -> tuple[int, object]:
    return (0, int(name)) if name.isdigit() else (1, name)


def migrate_video(
    profile: Profile,
    catalogue: Catalogue,
    video_id: str,
    *,
    apply: bool,
) -> VideoReport:
    """Settle one video, and say what was done to it."""
    if not catalogue.video_exists(video_id):
        return VideoReport(video_id, "not in the catalogue; left for the backfill's gc")

    broadcast_dir = parse_removable_path(f"{video_id}/{BROADCAST_DIR}")
    broadcast = _files_in(profile, (video_id, BROADCAST_DIR))
    if not broadcast:
        return VideoReport(video_id, f"{BROADCAST_DIR}/ holds no files; left alone")

    rows = [row for row in catalogue.files_for_video(video_id) if row.get("variant") == BROADCAST_VARIANT]

    if _files_in(profile, (video_id, ORIGINAL_DIR)):
        # Both present. The original is the one that is supposed to be there,
        # so the broadcast copy is redundant weight rather than a second
        # source -- and the rows that name it are about to name nothing.
        report = VideoReport(video_id, f"{ORIGINAL_DIR}/ already holds the source; {BROADCAST_DIR}/ is redundant")
        if apply:
            report.trashed = str(operations.trash(profile, broadcast_dir).destination)
            for row in rows:
                catalogue.unregister(row["id"])
        report.unregistered = [row["id"] for row in rows]
        return report

    if not rows:
        # Media with nothing claiming it. Moving it would be guessing that it
        # is this video's source, and registering it would be inventing a
        # record from a file -- so say so and leave it where it is.
        return VideoReport(video_id, f"{BROADCAST_DIR}/ holds media with no videofile row; left alone")

    return _promote_to_original(profile, catalogue, video_id, broadcast, rows, broadcast_dir, apply=apply)


def _promote_to_original(
    profile: Profile,
    catalogue: Catalogue,
    video_id: str,
    broadcast: list[str],
    rows: list[dict],
    broadcast_dir,
    *,
    apply: bool,
) -> VideoReport:
    """Broadcast only, and something claims it: this is the source."""
    report = VideoReport(video_id, f"{BROADCAST_DIR}/ is the source; moving it to {ORIGINAL_DIR}/")

    # File by file rather than as a directory, so an empty original/ left
    # behind by something else is not in the way.
    for name in broadcast:
        source = parse_file_path(f"{video_id}/{BROADCAST_DIR}/{name}", what="source")
        destination = parse_file_path(f"{video_id}/{ORIGINAL_DIR}/{name}", what="destination")
        if apply:
            operations.move(profile, source, destination)
        report.moved.append(str(source))

    for row in rows:
        # The basename, not the whole recorded path: the row may name the file
        # under any prefix the old system used, and where it is now is what
        # this just decided.
        filename = str(PurePosixPath(video_id) / ORIGINAL_DIR / PurePosixPath(row["filename"]).name)
        if apply:
            catalogue.retag(row["id"], variant=ORIGINAL_VARIANT, filename=filename)
        report.retagged.append(row["id"])

    if apply:
        report.trashed = str(operations.trash(profile, broadcast_dir).destination)
    return report


def _files_in(profile: Profile, parts: tuple[str, ...]) -> list[str]:
    """The names of the files -- not the subdirectories -- directly inside."""
    with SafeRoot(profile.root) as archive:
        if archive.lstat(parts) is None:
            return []
        with archive.directory(parts) as directory:
            names = []
            for name in sorted(os.listdir(directory)):
                info = os.lstat(name, dir_fd=directory)
                if stat.S_ISREG(info.st_mode):
                    names.append(name)
            return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fk-archive-migrate-broadcast",
        description="Move every video's source out of the legacy broadcast/ directory. One-shot.",
    )
    parser.add_argument("profile", help=f"archive profile to act on, as named in {PROFILE_DIR}")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="carry the migration out; without it nothing is changed and the plan is printed",
    )
    parser.add_argument(
        "--video",
        action="append",
        dest="videos",
        metavar="ID",
        help="only this video; repeatable. Without it, every video with a broadcast/ directory.",
    )
    parser.add_argument("--limit", type=int, help="stop after this many videos")
    parser.add_argument(
        "--environment",
        help="fk-cli environment to authenticate as. Defaults to the profile name, which is what "
        "keeps a migration of the production archive from being recorded against staging.",
    )
    parser.add_argument("--config", help="fk-cli configuration file (default ~/.frikanalen.yaml)")
    parser.add_argument("--api-url", help="django-api base URL, if the configuration file has none")
    return parser


def run(argv: list[str], stdout=None, profile_dir=PROFILE_DIR) -> int:
    args = build_parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout

    try:
        profile = load(args.profile, profile_dir=profile_dir)
        # Checked before anything is opened or any credential is read: a
        # mistyped --video should cost an error message, not a token lookup.
        for video_id in args.videos or ():
            if not video_id.isdigit():
                raise UsageError(f"{video_id!r} is not a video id")

        environment = args.environment or profile.name
        # Before dropping privileges: the file belongs to whoever ran this.
        credentials = load_credentials(environment, config_path=args.config, api_url=args.api_url)
        drop_to_manager(profile)

        videos = args.videos or find_candidates(profile)
    except ArchiveUtilsError as error:
        print(f"fk-archive-migrate-broadcast: {error}", file=sys.stderr)
        return error.exit_code

    if args.limit is not None:
        videos = videos[: args.limit]

    catalogue = Catalogue(credentials, dry_run=not args.apply)
    print(
        f"{len(videos)} videos with a {BROADCAST_DIR}/ directory in {profile.name} "
        f"({profile.root}), against {credentials.api_url} as {environment}.",
        file=stdout,
    )
    if not args.apply:
        print("Nothing will be changed. Re-run with --apply once this looks right.\n", file=stdout)

    reports = []
    for video_id in videos:
        try:
            report = migrate_video(profile, catalogue, video_id, apply=args.apply)
        except (ArchiveUtilsError, OSError) as error:
            # One video failing is not a reason to stop: the rest are
            # independent, and a run that gets through the other 4000 is worth
            # more than one that stops at the first oddity.
            report = VideoReport(video_id, "failed", failed=str(error))
        reports.append(report)
        print(report.describe(), file=stdout)

    failed = [report for report in reports if report.failed]
    print(f"\n{len(reports)} videos considered, {len(failed)} failed.", file=stdout)
    return 1 if failed else 0


def main() -> None:
    os.umask(0o022)
    sys.exit(run(sys.argv[1:]))
