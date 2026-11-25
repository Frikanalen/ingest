"""Archiving a recording that MediaMTX made on request.

Django calls POST /internal/recordings/<id> when a recording request has
ended. The call carries nothing but the id: what to fetch comes from asking
Django back with ingest's own credential, so a caller in the cluster can at
most make ingest re-fetch what Django says a request recorded.

From there it is an upload in all but name: the requested window is fetched
from MediaMTX's playback server into the upload space, probed,
and handed to the same Ingester an upload goes through, which archives it
as the video's original and queues its formats. This runs here, in the
upload pod, not as a worker job: the only worker pool is busy with long
encodes it doesn't interrupt, and a recording is wanted on air within
minutes.

The playback server's /get stops at the first break in a recording, and
MediaMTX breaks one whenever its source drifts from wall-clock time (a pull
relay that falls behind and catches up does that several times an hour).
So the window is first listed (/list), each unbroken run fetched on its own,
and the runs joined; an archive much shorter than what was recorded fails
loudly instead of passing as the whole thing.

When Django names a master instead (the source's own file of the same
programme, published afterwards), that file is fetched as it is and the
recording left alone.
"""

import asyncio
import datetime
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

#: The joined archive may come up this much short of what /list says was
#: recorded (joins drop a fraction of a second each) before it counts as lost.
MIN_SHARE = 0.9

#: Archives running now, by request id, so a repeated call while one runs is
#: not a second fetch of the same window.
_running: dict[int, asyncio.Task] = {}


async def _django(django_api: DjangoApiService, method: str, path: str, **kwargs) -> httpx.Response:
    client = django_api.client.get_async_httpx_client()
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response


def _client(url: str) -> tuple[httpx.AsyncClient, httpx.URL]:
    """A client carrying the URL's credential as a header, and the URL without
    it, so that httpx's request log never shows the playback token."""
    parsed = httpx.URL(url)
    auth = (parsed.username, parsed.password) if parsed.username else None
    return httpx.AsyncClient(timeout=FETCH_TIMEOUT, auth=auth), parsed.copy_with(userinfo=b"")


async def fetch(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    client, bare = _client(url)
    async with client, client.stream("GET", bare) as response:
        response.raise_for_status()
        with target.open("wb") as file:
            async for chunk in response.aiter_bytes(CHUNK):
                file.write(chunk)


def _parse_time(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _with_query(url: str, path: str, **params: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rsplit("/", 1)[0] + path, urlencode(query), ""))


async def list_runs(get_url: str) -> list[tuple[str, float]]:
    """The window's unbroken runs, as (/get URL, seconds), clipped to it.

    `get_url` is the /get URL Django gave for the whole window; the list and
    each run's URL keep its credential.
    """
    query = dict(parse_qsl(urlsplit(get_url).query))
    start = _parse_time(query["start"])
    end = start + datetime.timedelta(seconds=float(query["duration"]))
    list_url = _with_query(
        _strip(get_url, ("duration", "format")), "/list", start=start.isoformat(), end=end.isoformat()
    )
    client, bare = _client(list_url)
    async with client:
        response = await client.get(bare)
        if response.status_code == 404:  # nothing recorded in the window
            return []
        response.raise_for_status()
        entries = response.json()
    runs = []
    for entry in entries:
        run_start = max(_parse_time(entry["start"]), start)
        run_end = min(_parse_time(entry["start"]) + datetime.timedelta(seconds=float(entry["duration"])), end)
        seconds = (run_end - run_start).total_seconds()
        if seconds > 0:
            runs.append((_with_query(get_url, "/get", start=run_start.isoformat(), duration=f"{seconds:g}"), seconds))
    return runs


def _strip(url: str, keys: tuple[str, ...]) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in keys]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def _ffmpeg(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {stderr.decode(errors='replace')[-300:]}")
    return stdout.decode()


async def remux(sources: list[Path], target: Path) -> None:
    """Join fragmented MP4 runs into one plain MP4, streams copied as they are."""
    listing = target.with_suffix(".txt")
    listing.write_text("".join(f"file '{source.resolve()}'\n" for source in sources))
    try:
        await _ffmpeg(
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-map", "0", "-c", "copy", "-movflags", "+faststart", str(target),
        )  # fmt: skip
    finally:
        listing.unlink(missing_ok=True)


async def duration(path: Path) -> float:
    out = await _ffmpeg("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path))
    return float(out.strip() or 0)


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
        if recording.get("masterUrl"):  # the source's own file: fetched as it is
            logger.info("Fetching the master of recording %s", recording_id)
            await fetch(recording["masterUrl"], target)
            window = (_parse_time(recording["endtime"]) - _parse_time(recording["starttime"])).total_seconds()
            archived = await duration(target)
            if archived < MIN_SHARE * window:  # e.g. still being uploaded at the source
                raise RuntimeError(f"The master is only {archived:.0f} s of a {window:.0f} s window")
        else:
            runs = await list_runs(recording["playbackUrl"])
            if not runs:
                raise RuntimeError("Nothing was recorded in the window")
            recorded = sum(seconds for _, seconds in runs)
            pieces = [target.with_suffix(f".{n}.fmp4") for n in range(len(runs))]
            try:
                for (url, _), piece in zip(runs, pieces, strict=True):
                    await fetch(url, piece)
                await remux(pieces, target)
            finally:
                for piece in pieces:
                    piece.unlink(missing_ok=True)
            archived = await duration(target)
            logger.info(
                "Recording %s: %d run(s), %.0f s recorded, %.0f s archived", recording_id, len(runs), recorded, archived
            )
            if archived < MIN_SHARE * recorded:
                raise RuntimeError(f"Archived only {archived:.0f} s of {recorded:.0f} s recorded")
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
