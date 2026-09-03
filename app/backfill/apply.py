"""Carrying out a plan.

The plan says what; this says how. Kept apart from the chores because deciding
and doing have very different testability: one is arithmetic over observed
state, the other moves gigabytes between hosts.

The source file is fetched once, for any plan that has work in it, and read
from the archive rather than from anything the plan carried -- so a directory
this same plan has already swapped out is reflected. A plan with no actions
fetches nothing, which matters because that is exactly the case where there may
be no original to fetch.
"""

from dataclasses import replace
from logging import getLogger
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from app.api.hooks.metadata import MetadataExtractor
from app.archive_store import ArchiveSession
from app.backfill.actions import Action, ProduceFormat, RefreshMetadata
from app.backfill.chores import ORIGINAL_DIR, Plan
from app.django_client.service import DjangoApiService
from app.media.produce import FormatProducer, SourceMedia
from app.media.segmentation import segmentation_for
from app.runner import ProgressCallback

logger = getLogger(__name__)

#: The frame rate field is thousandths of a frame per second, so 25fps is
#: 25000 and 59.94 is 59940.
FRAME_RATE_SCALE = 1000


class SourceUnavailable(RuntimeError):
    """The plan needs the original and the archive does not have exactly one."""


class Applier:
    """Executes a plan for one video against the archive and django-api."""

    def __init__(
        self,
        archive: ArchiveSession,
        django_api: DjangoApiService,
        work_dir: Path | None = None,
    ):
        self.archive = archive
        self.django_api = django_api
        self.work_dir = work_dir
        self.producer = FormatProducer(archive, django_api)

    async def apply(
        self,
        plan: Plan,
        on_progress: ProgressCallback | None = None,
        source: SourceMedia | None = None,
    ) -> None:
        """Carry out `plan`, fetching the original only if something needs it.

        `source` is for the caller that already has the original locally: an
        upload still sitting where tusd left it, probed and measured on the way
        in. Passing it is what keeps that path from pulling back off the
        archive the gigabytes it has just finished putting there -- and from
        measuring the same file's loudness twice.
        """
        with TemporaryDirectory(dir=self.work_dir, prefix=f"apply-{plan.video_id}-") as scratch:
            # Once, up front, for any plan that has work in it: every action
            # there is derives something from the source file. A plan with no
            # actions fetches nothing, which is the case where there may well
            # be no original to fetch.
            if plan.actions and source is None:
                source = await self._fetch_source(plan.video_id, Path(scratch))

            for action in plan.actions:
                logger.info("video %s: %s", plan.video_id, action.describe())
                await self._apply(action, plan.video_id, source, Path(scratch), on_progress)

    async def _fetch_source(self, video_id: str, scratch: Path) -> SourceMedia:
        """Bring the original down and learn everything the formats need from it.

        Read from the archive rather than from anything the plan carried, so
        that a rename earlier in the same plan is already reflected. One fetch
        serves the probe, the loudness measurement and every format.
        """
        from app.media.loudness.measure import measure_loudness

        original_dir = PurePosixPath(video_id) / ORIGINAL_DIR
        files = [entry for entry in await self.archive.list_dir(original_dir) if not entry.is_dir]

        if len(files) != 1:
            raise SourceUnavailable(f"{original_dir} holds {len(files)} files, expected exactly one")

        local = scratch / files[0].name
        await self.archive.get(files[0].path, local)

        source = SourceMedia.probed(video_id, local, await MetadataExtractor().do_probe(local))
        if source.has_audio:
            source = replace(source, loudness=await measure_loudness(local))
        return source

    async def _apply(
        self,
        action: Action,
        video_id: str,
        source: SourceMedia | None,
        scratch: Path,
        on_progress: ProgressCallback | None,
    ) -> None:
        match action:
            case ProduceFormat(file_format=file_format, replacing=replacing):
                assert source is not None, "produce needs the original"
                if replacing is not None:
                    # Swapped, not overwritten: a new revision can emit a
                    # different set of files, and the old ones would otherwise
                    # sit beside the new output forever.
                    await self.archive.trash(replacing)
                await self.producer.produce(source, file_format, scratch, on_progress=on_progress)

            case RefreshMetadata(fields=fields, original_file_id=file_id):
                assert source is not None, "refreshing metadata needs the original"
                await self._refresh(fields, video_id, file_id, source)

            case _:
                raise NotImplementedError(f"no way to apply {action!r}")

    async def _refresh(self, fields: tuple[str, ...], video_id: str, file_id: int, source: SourceMedia) -> None:
        if "duration" in fields:
            await self.django_api.set_video_duration(video_id, source.metadata.format.duration)

        if "framerate" in fields:
            rate = segmentation_for(source.metadata).frame_rate
            await self.django_api.set_video_framerate(video_id, round(rate * FRAME_RATE_SCALE))

        if "loudness" in fields:
            if source.loudness is None:
                # Nothing to write: the track is silent, or loudnorm reported a
                # figure that is not finite. Said out loud because the column is
                # left exactly as it was found, and NULL there cannot be told
                # apart from never having measured at all -- which is why
                # refresh_metadata will not ask for the original again on that
                # basis alone. Without this line the whole round trip -- fetch,
                # probe, decode -- looks like it succeeded.
                logger.warning(
                    "video %s: the original has no measurable loudness; leaving it unrecorded",
                    video_id,
                )
            else:
                await self.django_api.set_video_file_loudness(file_id, source.loudness)
