"""`fk-archive-gc` -- reclaim media for videos the catalogue no longer has.

A video deleted from django-api leaves its directory in the archive behind, and
nothing else will ever collect it: everything else this system does is keyed on
a video that exists, and an ingest job belongs to a video, so a deleted one has
no job and never will. What this needs is not a job but a comparison of two
whole collections, run where both can be read.

Two guards, both about blast radius, because this is the one operation here
whose subject is the entire archive:

* the catalogue read refuses to hand back a partial answer -- absence is read
  as permission, so half a catalogue would make the archive look like garbage
  in exactly the proportion the read fell short by;
* the share of the archive about to be trashed is checked once, before
  anything moves.

Nothing is destroyed. Collecting a video is a rename into `.trash/`, and
`fk-archive-purge-trash` is what eventually reclaims the space -- so the
window in which a wrong answer here can still be undone is however long the
trash is kept.
"""

import argparse
import os
import stat
import sys
from dataclasses import dataclass, field

from fk_archive_utils import operations, operator
from fk_archive_utils.archive_path import parse_removable_path
from fk_archive_utils.catalogue import Catalogue
from fk_archive_utils.errors import ArchiveUtilsError, UsageError
from fk_archive_utils.profile import PROFILE_DIR, Profile
from fk_archive_utils.safe_root import SafeRoot

#: How much of the archive may turn out to be unaccounted for before this
#: stops and asks. Two percent is a guess at "a normal amount of deletion
#: since last time" -- it is meant to be crossed occasionally and thought
#: about, not never crossed.
DEFAULT_MAX_DELETE_FRACTION = 0.02


class TooMuchGarbage(ArchiveUtilsError):
    """More of the archive is unaccounted for than a sweep will act on alone."""

    exit_code = 9


@dataclass
class Orphan:
    video_id: str
    bytes_held: int
    trashed_to: str | None = None


@dataclass
class Report:
    archived: int = 0
    orphans: list[Orphan] = field(default_factory=list)

    @property
    def share(self) -> float:
        return len(self.orphans) / self.archived if self.archived else 0.0

    @property
    def reclaimable_bytes(self) -> int:
        return sum(orphan.bytes_held for orphan in self.orphans)


def archived_video_ids(profile: Profile) -> list[str]:
    """The video directories in the archive, ignoring its own bookkeeping.

    `.spool` and `.trash` are excluded by the same rule that excludes anything
    else: a video directory is named by a number, and nothing else here is.
    """
    with SafeRoot(profile.root) as archive, archive.directory(()) as root:
        return sorted((name for name in os.listdir(root) if name.isdigit()), key=int)


def sweep(
    profile: Profile,
    catalogue: Catalogue,
    *,
    apply: bool,
    max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
) -> Report:
    """Compare the archive against the catalogue and reclaim the difference."""
    # Read first, and in full. IncompleteCatalogue rather than a short answer.
    known = catalogue.video_ids()
    archived = archived_video_ids(profile)

    report = Report(archived=len(archived))
    report.orphans = [
        Orphan(video_id, _bytes_held(profile, video_id)) for video_id in archived if video_id not in known
    ]

    # Once, before anything moves. The orphan set is fully known by now, and
    # the whole point of the check is the total rather than any individual
    # decision in it.
    if apply:
        _refuse_if_too_much(report, max_delete_fraction, profile, catalogue)
        for orphan in report.orphans:
            result = operations.trash(profile, parse_removable_path(orphan.video_id))
            orphan.trashed_to = str(result.destination)

    return report


def _refuse_if_too_much(report: Report, limit: float, profile: Profile, catalogue: Catalogue) -> None:
    """Stop if the archive disagrees with the catalogue more than expected.

    The failure this is really for is not a bug in any of the above: it is the
    archive and the catalogue being different environments. Every individual
    decision is then locally correct -- that video really is not in that
    catalogue -- and only the total is insane, so the total is what has to be
    looked at.

    The environment defaulting to the archive profile's own name makes that
    hard to arrange by accident, but not impossible -- --environment exists --
    and a genuine mass deletion in the catalogue should stop and ask anyway.
    """
    if report.share <= limit:
        return

    raise TooMuchGarbage(
        f"{len(report.orphans)} of {report.archived} archived videos "
        f"({report.share:.1%}) are not in the catalogue, above the "
        f"{limit:.1%} this will act on unasked. Check that the {profile.name} archive "
        f"({profile.root}) and {catalogue.credentials.api_url} are the same environment; "
        f"if they are, raise --max-delete-fraction deliberately."
    )


def _bytes_held(profile: Profile, video_id: str) -> int:
    """How much this video is holding, so the report can say what is at stake."""
    total = 0
    with SafeRoot(profile.root) as archive:
        for directory, _, names in _walk(archive, (video_id,)):
            with archive.directory(directory) as fd:
                for name in names:
                    total += os.lstat(name, dir_fd=fd).st_size
    return total


def _walk(archive: SafeRoot, parts: tuple[str, ...]):
    """Descend `parts`, never following a symbolic link out of the archive."""
    with archive.directory(parts) as fd:
        entries = [(name, os.lstat(name, dir_fd=fd).st_mode) for name in os.listdir(fd)]

    directories = [name for name, mode in entries if stat.S_ISDIR(mode)]
    files = [name for name, mode in entries if stat.S_ISREG(mode)]
    yield parts, directories, files

    for name in directories:
        yield from _walk(archive, (*parts, name))


def build_parser() -> argparse.ArgumentParser:
    parser = operator.add_arguments(
        argparse.ArgumentParser(
            prog="fk-archive-gc",
            description="Move media for videos the catalogue no longer has into .trash/.",
        )
    )
    parser.add_argument(
        "--max-delete-fraction",
        type=float,
        default=DEFAULT_MAX_DELETE_FRACTION,
        help="refuse to sweep if more than this share of the archive is unaccounted for. "
        "Crossing it usually means the archive and the catalogue are different environments.",
    )
    return parser


def run(argv: list[str], stdout=None, profile_dir=PROFILE_DIR) -> int:
    args = build_parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout

    try:
        if args.max_delete_fraction < 0:
            raise UsageError("--max-delete-fraction must not be negative")

        context = operator.prepare(args, profile_dir=profile_dir)
        profile = context.profile
        report = sweep(profile, context.catalogue, apply=args.apply, max_delete_fraction=args.max_delete_fraction)
    except ArchiveUtilsError as error:
        print(f"fk-archive-gc: {error}", file=sys.stderr)
        return error.exit_code
    except OSError as error:
        print(f"fk-archive-gc: {error}", file=sys.stderr)
        return ArchiveUtilsError.exit_code

    for orphan in report.orphans:
        where = f" -> {orphan.trashed_to}" if orphan.trashed_to else ""
        print(f"  {orphan.video_id} ({human_bytes(orphan.bytes_held)}){where}", file=stdout)

    print(
        f"\n{len(report.orphans)} of {report.archived} archived videos are not in the catalogue "
        f"({report.share:.1%}), holding {human_bytes(report.reclaimable_bytes)}.",
        file=stdout,
    )
    if args.apply:
        print(f"{len(report.orphans)} moved into .trash/, recoverable until purged.", file=stdout)
    else:
        print("Nothing was changed. Re-run with --apply to move these into .trash/.", file=stdout)
    return 0


def human_bytes(count: float) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(count) < 1000 or unit == "TB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1000.0
    return f"{count} B"


def main() -> None:
    os.umask(0o022)
    sys.exit(run(sys.argv[1:]))
