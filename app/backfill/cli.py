"""The terminal end of a backfill.

`plan` reads the catalogue and the archive, works out what every video needs,
and prints it. `apply` does the same and then puts those videos in the queue
for the worker pool to drain. Neither does the work itself: what a video needs
is decided again by whichever worker claims it, so nothing here can hand a
worker a stale instruction, and closing the terminal stops nothing.

That is the whole of it, and the constraint that keeps it that way is worth
saying: everything either subcommand can decide is something an ingest job can
carry out, because an ingest job belongs to a video and every chore is about a
video that exists. Reclaiming media for a video the catalogue has *deleted*
fits neither half -- there is no job to queue and no worker to claim it -- so
it is not here at all. It is `fk-archive-gc`, run on the storage host, where
the archive is.
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
from app.django_client.service import DjangoApiService
from app.util.lifespan import get_token
from app.util.settings import get_settings

logger = logging.getLogger("backfill")


class Summary:
    """What a whole run came to, counted as it goes."""

    def __init__(self):
        self.videos = 0
        self.with_work = 0
        self.actions: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()

    def add(self, result: Plan) -> None:
        self.videos += 1
        if not result:
            return
        self.with_work += 1
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
        if self.with_work:
            lines += [
                "",
                "  Every one of those fetches the original and re-encodes from it,",
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
    """Which videos to look at: the catalogue's, and only the catalogue's.

    Never the archive's. Every chore is about a video that exists and returns
    early on one that does not, so listing the archive would buy a directory
    listing per orphan to learn that nothing will be done about it. What to do
    about an archived video the catalogue has dropped is `fk-archive-gc`'s
    question, and it is asked on the host holding the archive.
    """
    if args.video_id:
        return args.video_id

    ordered = sorted(snapshot.videos, key=int)
    return ordered[: args.limit] if args.limit else ordered


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

    report = await Enqueuer(django_api, priority=args.priority).enqueue_all(r.video_id for r in wanted)
    print()
    print(report.describe())
    print("\nDrain it by scaling the pool: kubectl scale deployment/ingest-workers --replicas=N")
    return 1 if report.failed else 0


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments shared by `plan` and `apply`.

    Shared entire, now that both subcommands consider the same chores: what a
    worker will run when it claims a video is the only list there is, so there
    is nothing for the two to disagree about.
    """
    parser.add_argument("video_id", nargs="*", help="videos to look at; omit for all of them")
    parser.add_argument("--limit", type=int, help="stop after this many videos")
    parser.add_argument(
        "--chore",
        action="append",
        choices=list(CHORES),
        help=(
            "consider only these chores when deciding whether a video needs anything. "
            "Repeatable, defaults to all of them. "
            "Note that this selects which videos are queued, not what happens to them: "
            "a worker that claims one re-plans it and does everything it turns out to need."
        ),
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="print the summary only")

    # Not `default=`: with action="append" argparse appends to the default
    # rather than replacing it, so one `--chore formats` would mean "all of
    # them, and formats too". main() substitutes this when none were given.
    parser.set_defaults(default_chores=tuple(CHORES))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fk-backfill", description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    planner = subcommands.add_parser("plan", help="say what the catalogue needs, and change nothing")
    _add_selection_arguments(planner)
    planner.set_defaults(handler=_run_plan, enqueue=False)

    # The same chores as `plan`, deliberately: `apply` can only offer a video
    # to the queue, so it must plan exactly what a worker will run when it
    # claims one, and there is only one list for either to read.
    applier = subcommands.add_parser("apply", help="queue the work for the worker pool")
    _add_selection_arguments(applier)
    applier.add_argument(
        "--priority",
        type=int,
        default=0,
        help="claim order among waiting jobs; higher is sooner. Leave at 0 so a member's upload goes first.",
    )
    applier.set_defaults(handler=_run_plan, enqueue=True)

    return parser


def _gc_moved() -> int:
    """Say where garbage collection went, rather than what argparse would say.

    A tombstone, intercepted ahead of the parser rather than declared as a
    subcommand: `fk-backfill gc --yes` is what a runbook actually says, and a
    subparser would answer that with "unrecognized arguments". Delete this
    once typing it has stopped being a thing anyone does.
    """
    print(
        "Garbage collection is `fk-archive-gc` now, and runs on the storage host.\n"
        "\n"
        "It never fitted here: an ingest job belongs to a video, so a video the\n"
        "catalogue has deleted has no job to queue and no worker to claim it --\n"
        "and doing it from here meant this holding a standing permission to\n"
        "remove any directory in the archive.\n"
        "\n"
        "    ssh file01 sudo fk-archive-gc prod\n"
        "    ssh file01 sudo fk-archive-gc prod --apply",
        file=sys.stderr,
    )
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["gc"]:
        return _gc_moved()

    args = build_parser().parse_args(argv)
    if getattr(args, "chore", None) is None and hasattr(args, "default_chores"):
        args.chore = list(args.default_chores)

    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
