"""Reclaiming media the catalogue no longer has a video for.

The only chore that cannot go through the queue. An ingest job is keyed on the
video it belongs to, so a video the catalogue has deleted has no job and never
will -- there is nothing to enqueue and nothing to claim. What garbage
collection needs is not a job but a comparison of two whole collections, which
is what this is.

It is also the one operation whose blast radius is the whole archive, so it is
guarded twice: the catalogue read refuses to hand back a partial answer, and
the proportion about to be trashed is checked before anything moves.
"""

from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path

from app.archive_store import ArchiveStore
from app.backfill.apply import Applier
from app.backfill.chores import DesiredState, plan
from app.backfill.observe import Observer
from app.django_client.service import DjangoApiService

logger = getLogger(__name__)

#: How much of the archive may turn out to be garbage before this stops and
#: asks. Two percent is a guess at "a normal amount of deletion since last
#: time" -- it is meant to be crossed occasionally and thought about, not
#: never crossed.
DEFAULT_MAX_DELETE_FRACTION = 0.02


class TooMuchGarbage(RuntimeError):
    """More of the archive is unaccounted for than a sweep will act on alone."""


@dataclass
class SweepReport:
    archived: int = 0
    orphans: list[str] = field(default_factory=list)
    reclaimable_bytes: int = 0
    trashed: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        return len(self.orphans) / self.archived if self.archived else 0.0


class Sweep:
    """Compares the archive against the catalogue and reclaims the difference."""

    def __init__(
        self,
        archive: ArchiveStore,
        django_api: DjangoApiService,
        *,
        max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
        work_dir: Path | None = None,
    ):
        self.archive = archive
        self.django_api = django_api
        self.max_delete_fraction = max_delete_fraction
        self.work_dir = work_dir

    async def run(self, *, apply: bool) -> SweepReport:
        async with self.archive.open() as archive:
            observer = Observer(archive, self.django_api)

            # Raises IncompleteSnapshot rather than returning what arrived. A
            # catalogue read that came up short would make the archive look
            # like garbage in exactly the proportion it fell short by.
            snapshot = await observer.snapshot()
            archived = await observer.archived_video_ids()

            report = SweepReport(archived=len(archived), orphans=[v for v in archived if v not in snapshot])
            logger.info(
                "%d of %d archived videos are not in the catalogue (%.1f%%)",
                len(report.orphans),
                report.archived,
                100 * report.share,
            )

            # Once, before anything moves. The orphan set is fully known by
            # now, and the whole point of the check is the total rather than
            # any individual decision in it.
            if apply:
                self._refuse_if_too_much(report)

            desired = DesiredState.from_templates()
            applier = Applier(archive, self.django_api, self.work_dir)

            for video_id in report.orphans:
                state = await observer.observe(video_id, snapshot)
                report.reclaimable_bytes += sum(
                    entry.size for contents in state.directories.values() for entry in contents if not entry.is_dir
                )

                if not apply:
                    continue

                work = plan(state, desired, chores=("gc",))
                if work:
                    await applier.apply(work)
                    report.trashed.append(video_id)

            return report

    def _refuse_if_too_much(self, report: SweepReport) -> None:
        """Stop if the archive disagrees with the catalogue more than expected.

        The failure this is really for is not a bug in any of the above: it is
        pointing FK_API_URL at one environment and FK_ARCHIVE_DIR at another.
        Every individual decision is then locally correct -- that video really
        is not in that catalogue -- and only the total is insane, so the total
        is what has to be looked at.
        """
        if report.share <= self.max_delete_fraction:
            return

        raise TooMuchGarbage(
            f"{len(report.orphans)} of {report.archived} archived videos "
            f"({report.share:.1%}) are not in the catalogue, above the "
            f"{self.max_delete_fraction:.1%} this will act on unasked. "
            f"Check that FK_API_URL and FK_ARCHIVE_DIR name the same environment; "
            f"if they do, raise --max-delete-fraction deliberately."
        )
