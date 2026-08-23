"""Carrying out a plan.

The plan says what; this says how. Kept apart from the chores because deciding
and doing have very different testability: one is arithmetic over observed
state, the other moves gigabytes between hosts.

The source file is fetched lazily, on the first action that needs it. That is
not just an optimisation -- a plan can rename `broadcast/` to `original/` and
then derive formats from the result, so fetching up front would look in a
directory that does not exist yet.
"""

from dataclasses import replace
from logging import getLogger
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from app.api.hooks.metadata import MetadataExtractor
from app.archive_store import ArchiveSession
from app.backfill.actions import (
    Action,
    MovePath,
    ProduceFormat,
    RefreshMetadata,
    RetagFile,
    TrashPath,
    UnregisterFile,
)
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

    async def apply(self, plan: Plan, on_progress: ProgressCallback | None = None) -> None:
        with TemporaryDirectory(dir=self.work_dir, prefix=f"backfill-{plan.video_id}-") as scratch:
            source: SourceMedia | None = None

            for action in plan.actions:
                if action.needs_original and source is None:
                    source = await self._fetch_source(plan.video_id, Path(scratch))

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
            case TrashPath(path=path):
                await self.archive.trash(path)

            case MovePath(source=origin, destination=destination):
                await self.archive.move(origin, destination)

            case RetagFile(file_id=file_id, variant=variant, filename=filename):
                await self.django_api.retag_video_file(file_id, variant, str(filename))

            case UnregisterFile(file_id=file_id):
                await self.django_api.delete_video_file(file_id)

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

        if "loudness" in fields and source.loudness is not None:
            await self.django_api.set_video_file_loudness(file_id, source.loudness)
