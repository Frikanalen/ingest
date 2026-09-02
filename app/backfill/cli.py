"""The terminal end of a backfill.

`plan` reads the catalogue and the archive, works out what every video needs,
and prints it. `apply` does the same and then puts those videos in the queue
for the worker pool to drain. Neither does the work itself: what a video needs
is decided again by whichever worker claims it, so nothing here can hand a
worker a stale instruction, and closing the terminal stops nothing.

`gc` is the exception, and has to be. Reclaiming media for a video the
catalogue has deleted cannot go through the queue -- an ingest job belongs to a
video, and that video is gone -- so the sweep does the work itself.
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from collections.abc import Sequence

from frikanalen_django_api_client import AuthenticatedClient

from app.archive_store import create_archive_store
from app.backfill.chores import CHORES, DesiredState, Plan, plan
from app.backfill.enqueue import Enqueuer
from app.backfill.observe import CatalogueSnapshot, Observer
from app.backfill.sweep import DEFAULT_MAX_DELETE_FRACTION, Sweep, TooMuchGarbage
from app.django_client.service import DjangoApiService
from app.util.lifespan import get_token
from app.util.settings import get_settings

logger = logging.getLogger("backfill")


def _bytes(count: float) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(count) < 1000 or unit == "TB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1000.0
    return f"{count} B"


class Summary:
    """What a whole run came to, counted as it goes."""

    def __init__(self):
        self.videos = 0
        self.with_work = 0
        self.actions: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()
        self.needing_original = 0

    def add(self, result: Plan) -> None:
        self.videos += 1
        if not result:
            return
        self.with_work += 1
        self.needing_original += result.needs_original
        for action in result.actions:
            self.actions[type(action).__name__] += 1
        for note in result.notes:
            # Notes name specific paths; count the kind, not the instance.
            self.notes[note.split(";")[0].split(" holds ")[0]] += 1

    def report(self) -> str:
        lines = ["", f"{self.videos} videos looked at, {self.with_work} need something done."]
        if self.actions:
            lines.append("")
            lines += [f"  {count:>7}  {name}" for name, count in self.actions.most_common()]
        if self.needing_original:
            lines += [
                "",
                f"  {self.needing_original} of them need the original fetched and re-encoded,",
                "  which is where essentially all of the wall-clock time goes.",
            ]
        if self.notes:
            lines += ["", "  reported, not acted on:"]
            lines += [f"  {count:>7}  {note}" for note, count in self.notes.most_common()]
        return "\n".join(lines)


async def _with_services(handler):
    """Open the archive and an authenticated API client, and hand them over."""
    settings = get_settings()

    archive_store = create_archive_store(settings.archive)
    logger.info("Reading archive at %s", archive_store)

    async with AuthenticatedClient(
        base_url=str(settings.api.url),
        token=get_token(settings.api),
        prefix="Token",
        raise_on_unexpected_status=True,
        follow_redirects=True,
    ) as client:
        return await handler(settings, archive_store, DjangoApiService(client))


async def _selection(args, observer: Observer, snapshot: CatalogueSnapshot) -> Sequence[str]:
    if args.video_id:
        return args.video_id

    # The union, deliberately: the catalogue holds videos whose derivatives may
    # be missing, and the archive holds directories the catalogue may no longer
    # have. Either side alone would miss one of the two things worth finding.
    ids = sorted(set(snapshot.videos) | set(await observer.archived_video_ids()), key=int)
    return ids[: args.limit] if args.limit else ids


async def _run_plan(args: argparse.Namespace) -> int:
    async def go(settings, archive_store, django_api):
        async with archive_store.open() as archive:
            observer = Observer(archive, django_api)

            logger.info("Reading the catalogue")
            snapshot = await observer.snapshot()

            video_ids = await _selection(args, observer, snapshot)
            logger.info("Planning %d videos", len(video_ids))

            summary = Summary()
            wanted: list[Plan] = []

            async for state in observer.observe_all(video_ids, snapshot):
                result = plan(state, DesiredState.from_templates(), chores=tuple(args.chore))
                summary.add(result)
                if result:
                    wanted.append(result)
                    if not args.quiet:
                        print(result.describe())

            print(summary.report())

            if not args.enqueue:
                print("\nNothing was changed. `apply` puts this work in the queue.")
                return 0

            return await _enqueue(args, django_api, wanted)

    return await _with_services(go)


async def _enqueue(args, django_api, wanted: list[Plan]) -> int:
    if not wanted:
        print("\nNothing to queue.")
        return 0

    destructive = [result for result in wanted if result.is_destructive]
    if destructive and not args.yes:
        print(
            f"\n{len(destructive)} of these move media out of the published tree. "
            f"Re-run with --yes once the plan above looks right."
        )
        return 1

    report = await Enqueuer(django_api, priority=args.priority).enqueue_all(r.video_id for r in wanted)
    print()
    print(report.describe())
    print("\nDrain it by scaling the pool: kubectl scale deployment/ingest-workers --replicas=N")
    return 1 if report.failed else 0


async def _run_gc(args: argparse.Namespace) -> int:
    async def go(settings, archive_store, django_api):
        sweep = Sweep(
            archive_store,
            django_api,
            max_delete_fraction=args.max_delete_fraction,
            work_dir=settings.work_dir,
        )

        try:
            report = await sweep.run(apply=args.yes)
        except TooMuchGarbage as refusal:
            print(f"\nRefusing to sweep: {refusal}")
            return 1

        print(
            f"\n{len(report.orphans)} of {report.archived} archived videos are not in the catalogue "
            f"({report.share:.1%}), holding {_bytes(report.reclaimable_bytes)}."
        )
        for video_id in report.orphans[:20]:
            print(f"  {video_id}")
        if len(report.orphans) > 20:
            print(f"  ... and {len(report.orphans) - 20} more")

        if args.yes:
            print(f"\n{len(report.trashed)} moved into .trash/, recoverable until purged.")
        else:
            print("\nNothing was changed. Pass --yes to move these into .trash/.")
        return 0

    return await _with_services(go)


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("video_id", nargs="*", help="videos to look at; omit for all of them")
    parser.add_argument("--limit", type=int, help="stop after this many videos")
    parser.add_argument(
        "--chore",
        action="append",
        choices=list(CHORES),
        help=(
            "consider only these chores when deciding whether a video needs anything. "
            "Repeatable, defaults to all of them. Note that this selects which videos "
            "are queued, not what happens to them: a worker that claims one re-plans it "
            "and does everything it turns out to need."
        ),
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="print the summary only")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fk-backfill", description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    planner = subcommands.add_parser("plan", help="say what the catalogue needs, and change nothing")
    _add_selection_arguments(planner)
    planner.set_defaults(handler=_run_plan, enqueue=False)

    applier = subcommands.add_parser("apply", help="queue the work for the worker pool")
    _add_selection_arguments(applier)
    applier.add_argument(
        "--priority",
        type=int,
        default=0,
        help="claim order among waiting jobs; higher is sooner. Leave at 0 so a member's upload goes first.",
    )
    applier.add_argument("--yes", action="store_true", help="confirm work that moves media out of the published tree")
    applier.set_defaults(handler=_run_plan, enqueue=True)

    collector = subcommands.add_parser("gc", help="reclaim media for videos the catalogue no longer has")
    collector.add_argument(
        "--max-delete-fraction",
        type=float,
        default=DEFAULT_MAX_DELETE_FRACTION,
        help=(
            "refuse to sweep if more than this share of the archive is unaccounted for. "
            "Crossing it usually means FK_API_URL and FK_ARCHIVE_DIR name different environments."
        ),
    )
    collector.add_argument("--yes", action="store_true", help="actually move the orphans into .trash/")
    collector.set_defaults(handler=_run_gc)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    args = build_parser().parse_args(argv)
    if getattr(args, "chore", None) is None and hasattr(args, "video_id"):
        args.chore = list(CHORES)

    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
