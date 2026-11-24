"""Archiving a recording that MediaMTX made on request.

Django calls POST /internal/recordings/<id> when a recording request has
ended. The call carries nothing but the id: what to fetch comes from asking
Django back with ingest's own credential, so a caller in the cluster can at
most make ingest re-fetch what Django says a request recorded.

From there it is an upload in all but name: the requested window is fetched
from MediaMTX's playback server as one MP4 into the upload space, probed,
and handed to the same Ingester an upload goes through, which archives it
as the video's original and queues its formats. This runs here, in the
upload pod, not as a worker job: the only worker pool is busy with long
encodes it doesn't interrupt, and a recording is wanted on air within
minutes.
"""

import asyncio
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends
from frikanalen_django_api_client.models import IngestStateEnum

from app.api.hooks.metadata import ComplianceError, MetadataExtractor
from app.archive_store import ArchiveStore
from app.django_client.service import DjangoApiService
from app.ingest import Ingester
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.util.app_state import get_archive_store, get_django_api, get_metadata_extractor
from app.util.settings import IngestAppSettings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

#: An hour at 10 Mbit/s is 4.5 GB; the playback server sends it as fast as
#: the volume reads, so allow the read plenty of time between chunks.
FETCH_TIMEOUT = httpx.Timeout(30.0, read=300.0)
CHUNK = 1 << 20

#: Archives running now, by request id, so a repeated call while one runs is
#: not a second fetch of the same window.
_running: dict[int, asyncio.Task] = {}


async def _django(django_api: DjangoApiService, method: str, path: str, **kwargs) -> httpx.Response:
    client = django_api.client.get_async_httpx_client()
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response


async def fetch(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as file:
            async for chunk in response.aiter_bytes(CHUNK):
                file.write(chunk)


async def remux(source: Path, target: Path) -> None:
    """Rewrite fragmented MP4 as a plain one, streams copied as they are."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-map", "0", "-c", "copy", "-movflags", "+faststart", str(target),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"remux failed: {stderr.decode(errors='replace')[-300:]}")


async def archive_recording(
    recording_id: int,
    settings: IngestAppSettings,
    django_api: DjangoApiService,
    archive: ArchiveStore,
    metadata_extractor: MetadataExtractor,
) -> None:
    recording = (await _django(django_api, "GET", f"/api/recordings/{recording_id}")).json()
    if recording.get("state") != "importing":
        logger.info("Recording %s is %s, not waiting for ingest", recording_id, recording.get("state"))
        return
    video_id = str(recording["video"])
    reporter = IngestReporter(django_api, video_id)
    target = settings.tusd_dir / video_id / f"recording-{recording_id}.mp4"
    ok, error = False, ""
    try:
        logger.info("Fetching recording %s for video %s", recording_id, video_id)
        fetched = target.with_suffix(".fmp4")
        try:
            await fetch(recording["playbackUrl"], fetched)
            await remux(fetched, target)
        finally:
            fetched.unlink(missing_ok=True)
        await reporter.state(IngestStateEnum.PROBING)
        try:
            metadata = await metadata_extractor.assert_compliance(target)
        except ComplianceError as e:
            await reporter.failed(IngestErrorCode.NOT_COMPLIANT, str(e))
            raise
        await Ingester(archive=archive, django_api=django_api).ingest(video_id, target, metadata, reporter)
        ok = True
    except Exception as e:  # reported to Django, which shows it and may ask again
        logger.exception("Could not archive recording %s", recording_id)
        error = str(e)[:500]
        await reporter.failed_unless_already(IngestErrorCode.INTERNAL_ERROR, error)
        target.unlink(missing_ok=True)
    await _django(django_api, "POST", f"/api/recordings/{recording_id}/result", json={"ok": ok, "error": error})


@router.post("/recordings/{recording_id}", status_code=202)
async def receive_recording(
    recording_id: int,
    settings: IngestAppSettings = Depends(get_settings),
    django_api: DjangoApiService = Depends(get_django_api),
    archive: ArchiveStore = Depends(get_archive_store),
    metadata_extractor: MetadataExtractor = Depends(get_metadata_extractor),
):
    running = _running.get(recording_id)
    if running is None or running.done():
        task = asyncio.create_task(archive_recording(recording_id, settings, django_api, archive, metadata_extractor))
        _running[recording_id] = task
        task.add_done_callback(lambda t: _log_failure(recording_id, t))
    return {}


def _log_failure(recording_id: int, task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Archiving recording %s failed: %r", recording_id, task.exception())
