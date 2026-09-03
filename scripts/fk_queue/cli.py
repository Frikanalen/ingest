"""What every queue-side tool does, which is nearly all of what each one does.

Read the catalogue, run one chore over every selected video, print what it came
to, and with `--apply` put those videos in the queue. A tool is then the chore
it runs and its name; there is no room for two of them to disagree about how a
catalogue is paged or how a job is queued.

Nothing here plans work a worker will not re-plan. The videos that come out of
this are the videos whose *catalogue rows* say they are short of something, and
the worker that claims one looks at the archive as well before deciding what to
actually do -- so the worst an over-eager selection costs is a job that turns
out to have nothing in it.
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from collections.abc import Mapping, Sequence

from frikanalen_django_api_client import AuthenticatedClient

from app.converge.chores import CHORES, DesiredState, Plan, plan
from app.converge.state import VideoState
from app.django_client.service import DjangoApiService
from fk_queue import catalogue, credentials
from fk_queue.enqueue import Enqueuer

logger = logging.getLogger("fk_queue")

#: What an operator's work goes in at. Below a member's upload, deliberately:
#: claiming hands out the highest-priority job waiting, so this is what a free
#: worker picks up once nobody is waiting on an upload.
DEFAULT_PRIORITY = 0


class Summary:
    """What a whole run came to, counted as it goes."""

    def __init__(self):
        self.videos = 0
        self.with_work = 0
        self.actions: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()

    def add(self, result: Plan) -> None:
        self.videos += 1

        if result:
            self.with_work += 1
            for action in result.actions:
                self.actions[type(action).__name__] += 1

        # Outside the `if`, because the videos worth reporting a note about are
        # very often the ones with nothing to do: a source still under
        # `broadcast/` is reported and derived nothing from, and counting notes
        # only for videos that also had work hid every one of them.
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


def build_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)

    parser.add_argument("video_id", nargs="*", help="videos to look at; omit for all of them")
    parser.add_argument("--limit", type=int, help="stop after this many videos")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="queue the work for the worker pool; without it nothing is changed and the plan is printed",
    )
    parser.add_argument(
        "--priority",
        type=int,
        default=DEFAULT_PRIORITY,
        help="claim order among waiting jobs; higher is sooner. Leave at 0 so a member's upload goes first.",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="print the summary only")
    parser.add_argument("--environment", help="fk-cli environment to authenticate as, default the file's own")
    parser.add_argument("--config", help=f"fk-cli configuration file (default {credentials.DEFAULT_CONFIG})")
    parser.add_argument("--api-url", help="django-api base URL, if the configuration file has none")

    return parser


def _selection(args, videos: Mapping[str, VideoState]) -> tuple[Sequence[str], Sequence[str]]:
    """Which videos to look at, and which of the asked-for ones do not exist.

    The catalogue's, and only the catalogue's. Every chore is about a video
    that exists, so a directory in the archive the catalogue has dropped is not
    this tool's subject: there is no job to queue for a video that has no row,
    and the PUT that tried would fail and land in the run's failure count.
    """
    if not args.video_id:
        ordered = sorted(videos, key=int)
        return (ordered[: args.limit] if args.limit else ordered), ()

    asked = [str(video_id) for video_id in args.video_id]
    return [v for v in asked if v in videos], [v for v in asked if v not in videos]


async def _run(args: argparse.Namespace, chore: str) -> int:
    creds = credentials.load(
        environment=args.environment,
        config_path=args.config,
        api_url=args.api_url,
    )
    logger.info("Reading the %s catalogue at %s", creds.environment, creds.api_url)

    async with AuthenticatedClient(
        base_url=creds.api_url,
        token=creds.token,
        prefix="Token",
        raise_on_unexpected_status=True,
        follow_redirects=True,
    ) as client:
        django_api = DjangoApiService(client)

        videos = await catalogue.read(django_api)
        selected, unknown = _selection(args, videos)

        for video_id in unknown:
            logger.warning("No video %s in the catalogue; ignoring it", video_id)

        logger.info("Planning %d videos", len(selected))

        desired = DesiredState.from_templates()
        summary = Summary()
        wanted: list[str] = []

        for video_id in selected:
            result = plan(videos[video_id], desired, chores=(chore,))
            summary.add(result)

            if result:
                wanted.append(video_id)
            if (result.actions or result.notes) and not args.quiet:
                print(result.describe())

        print(summary.report())

        if not args.apply:
            print("\nNothing was changed. `--apply` puts this work in the queue.")
            return 0

        return await _enqueue(args, django_api, wanted)


async def _enqueue(args, django_api: DjangoApiService, wanted: Sequence[str]) -> int:
    if not wanted:
        print("\nNothing to queue.")
        return 0

    report = await Enqueuer(django_api, priority=args.priority).enqueue_all(wanted)
    print()
    print(report.describe())
    print("\nDrain it by scaling the pool: kubectl scale deployment/ingest-workers --replicas=N")
    return 1 if report.failed else 0


def main(argv: Sequence[str] | None, *, prog: str, description: str, chore: str) -> int:
    """Run `chore` over the catalogue as `prog`.

    The chore is named rather than supplied, and it has to be one of `CHORES`:
    a tool here can only decide which videos to offer a worker, and a worker
    runs the chores in `CHORES`. One that named anything else would print a
    plan nothing was ever going to carry out.
    """
    if chore not in CHORES:
        raise ValueError(f"{chore!r} is not a chore any worker runs; try one of {sorted(CHORES)}")

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = build_parser(prog, description).parse_args(argv)

    try:
        return asyncio.run(_run(args, chore))
    except credentials.CredentialsError as e:
        logger.error("%s", e)
        return 2
