"""The terminal end of a backfill.

`plan` is the whole command for now. It reads the catalogue and the archive,
works out what every video needs, and prints it -- which is the thing worth
having before anything else exists, because it turns "the archive has drifted"
into a number, and that number decides whether this is a weekend or a month.

It changes nothing. Applying a plan is the worker pool's job, and enqueueing
work for it needs django-api's claim endpoint.
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
from app.backfill.observe import CatalogueSnapshot, Observer
from app.django_client.service import DjangoApiService
from app.util.lifespan import get_token
from app.util.settings import get_settings

logger = logging.getLogger("backfill")


def _bytes(count: int) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(count) < 1000 or unit == "TB":
            return f"{count:.1f} {unit}" if unit != "B" else f"{count} B"
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
        lines = [
            "",
            f"{self.videos} videos looked at, {self.with_work} need something done.",
        ]
        if self.actions:
            lines.append("")
            for name, count in self.actions.most_common():
                lines.append(f"  {count:>7}  {name}")
        if self.needing_original:
            lines += [
                "",
                f"  {self.needing_original} of them need the original fetched and re-encoded,",
                "  which is where essentially all of the wall-clock time goes.",
            ]
        if self.notes:
            lines += ["", "  reported, not acted on:"]
            for note, count in self.notes.most_common():
                lines.append(f"  {count:>7}  {note}")
        return "\n".join(lines)


async def _run_plan(args: argparse.Namespace) -> int:
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
        django_api = DjangoApiService(client)

        async with archive_store.open() as archive:
            observer = Observer(archive, django_api)

            logger.info("Reading the catalogue")
            snapshot = await observer.snapshot()

            video_ids = await _selection(args, observer, snapshot)
            logger.info("Planning %d videos", len(video_ids))

            return await _report(args, observer, snapshot, video_ids)


async def _selection(args, observer: Observer, snapshot: CatalogueSnapshot) -> Sequence[str]:
    if args.video_id:
        return args.video_id

    # The union, deliberately: the catalogue holds videos whose derivatives may
    # be missing, and the archive holds directories the catalogue may no longer
    # have. Either side alone would miss one of the two things worth finding.
    ids = sorted(set(snapshot.videos) | set(await observer.archived_video_ids()), key=int)
    return ids[: args.limit] if args.limit else ids


async def _report(args, observer: Observer, snapshot: CatalogueSnapshot, video_ids: Sequence[str]) -> int:
    desired = DesiredState.from_templates()
    summary = Summary()
    trashed_bytes = 0

    async for state in observer.observe_all(video_ids, snapshot):
        result = plan(state, desired, chores=tuple(args.chore))
        summary.add(result)

        if result and not args.quiet:
            print(result.describe())

        trashed_bytes += sum(
            entry.size for contents in state.directories.values() for entry in contents if not entry.is_dir
        ) * (not state.in_catalogue)

    print(summary.report())
    if trashed_bytes:
        print(f"\n  {_bytes(trashed_bytes)} of media belongs to videos the catalogue no longer has.")
    print("\nNothing was changed. Applying a plan needs the worker pool.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fk-backfill", description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)

    planner = subcommands.add_parser("plan", help="say what the catalogue needs, and change nothing")
    planner.add_argument("video_id", nargs="*", help="videos to look at; omit for all of them")
    planner.add_argument("--limit", type=int, help="stop after this many videos")
    planner.add_argument(
        "--chore",
        action="append",
        choices=list(CHORES),
        help="run only these chores; repeatable, defaults to all of them",
    )
    planner.add_argument("-q", "--quiet", action="store_true", help="print the summary only")
    planner.set_defaults(handler=_run_plan)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    args = build_parser().parse_args(argv)
    if not getattr(args, "chore", None):
        args.chore = list(CHORES)

    started = asyncio.get_event_loop_policy().new_event_loop()
    try:
        return started.run_until_complete(args.handler(args))
    finally:
        started.close()


if __name__ == "__main__":
    raise SystemExit(main())
