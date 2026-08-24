"""Draining the queue.

The loop itself is small; what these hold in place is what it does when things
go wrong. A worker that dies on a bad video stops draining the queue, and a
worker that abandons a claim the moment it is asked to stop throws away however
many hours have gone into it.
"""

import asyncio
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import IngestStateEnum, VideoFileVariantEnum

from app.archive_store import LocalArchiveStore
from app.backfill.apply import SourceUnavailable
from app.ingest_reporting import IngestErrorCode
from app.media.produce import TranscodeFailed
from app.worker import Worker

VIDEO_ID = "12345"


def job(video=int(VIDEO_ID)):
    return SimpleNamespace(video=video)


def video_row(duration="00:10:00", framerate=25000):
    return SimpleNamespace(id=int(VIDEO_ID), duration=duration, framerate=framerate)


def file_row(file_id, variant, filename, revision=1, lufs=-23.0):
    return SimpleNamespace(
        id=file_id,
        video=int(VIDEO_ID),
        variant=VideoFileVariantEnum(variant),
        filename=filename,
        integrated_lufs=lufs,
        profile_revision=revision,
        additional_properties={},
    )


def files(*rows):
    return SimpleNamespace(count=len(rows), results=list(rows))


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def django_api():
    """A catalogue where video 12345 is complete and needs nothing."""
    api = AsyncMock()
    api.claim_ingest_job.return_value = None
    api.get_video.return_value = video_row()
    api.get_files_for_video.return_value = files(
        file_row(1, "original", f"{VIDEO_ID}/original/source.mp4", revision=0),
        file_row(2, "dash", f"{VIDEO_ID}/dash/manifest.mpd"),
        file_row(3, "large_thumb", f"{VIDEO_ID}/large_thumb/source.jpg"),
        file_row(4, "med_thumb", f"{VIDEO_ID}/med_thumb/source.jpg"),
        file_row(5, "small_thumb", f"{VIDEO_ID}/small_thumb/source.jpg"),
    )
    return api


@pytest.fixture
def worker(archive_root, django_api, tmp_path):
    return Worker(
        LocalArchiveStore(archive_root),
        django_api,
        name="test-worker",
        kind="backfill",
        work_dir=tmp_path,
        poll_interval_s=0.01,
    )


def reported_states(django_api):
    return [call.args[1] for call in django_api.report_ingest_state.await_args_list]


@pytest.mark.asyncio
async def test_an_empty_queue_claims_nothing(worker, django_api):
    assert await worker.run_once() is False
    django_api.report_ingest_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_worker_says_what_it_can_reach(worker, django_api):
    """An upload's source is in a volume only one pod has, so a worker that
    cannot reach it must not be handed one."""
    await worker.run_once()

    django_api.claim_ingest_job.assert_awaited_once_with(worker="test-worker", kind="backfill")


@pytest.mark.asyncio
async def test_a_video_that_needs_nothing_is_finished_not_skipped(worker, django_api):
    django_api.claim_ingest_job.side_effect = [job(), None]

    assert await worker.run_once() is True
    assert reported_states(django_api)[-1] == IngestStateEnum.DONE


@pytest.mark.asyncio
async def test_work_that_is_needed_gets_done(worker, django_api, archive_root, color_bars_video):
    """A thumbnail registered by a superseded profile is rebuilt."""
    (archive_root / VIDEO_ID / "original").mkdir(parents=True)
    (archive_root / VIDEO_ID / "original" / "source.mp4").write_bytes(color_bars_video.read_bytes())

    django_api.claim_ingest_job.side_effect = [job(), None]
    django_api.get_files_for_video.return_value = files(
        file_row(1, "original", f"{VIDEO_ID}/original/source.mp4", revision=0)
    )

    await worker.run_once()

    assert (archive_root / VIDEO_ID / "large_thumb" / "source.jpg").exists()
    assert (archive_root / VIDEO_ID / "dash" / "manifest.mpd").exists()
    assert reported_states(django_api)[-1] == IngestStateEnum.DONE


@pytest.mark.asyncio
async def test_a_failing_video_is_reported_and_does_not_stop_the_worker(worker, django_api, monkeypatch):
    """One video that cannot be processed is not a reason to stop the rest."""
    django_api.claim_ingest_job.side_effect = [job(), None]

    async def explode(*args, **kwargs):
        raise TranscodeFailed("ffmpeg gave up")

    monkeypatch.setattr("app.worker.Observer.observe_one", explode)

    assert await worker.run_once() is True

    [failure] = [c for c in django_api.report_ingest_state.await_args_list if c.args[1] == IngestStateEnum.FAILED]
    assert failure.kwargs["error_code"] == str(IngestErrorCode.TRANSCODE_FAILED)


@pytest.mark.asyncio
async def test_a_missing_source_is_an_archive_failure(worker, django_api, monkeypatch):
    django_api.claim_ingest_job.side_effect = [job(), None]

    async def explode(*args, **kwargs):
        raise SourceUnavailable("no original")

    monkeypatch.setattr("app.worker.Observer.observe_one", explode)
    await worker.run_once()

    [failure] = [c for c in django_api.report_ingest_state.await_args_list if c.args[1] == IngestStateEnum.FAILED]
    assert failure.kwargs["error_code"] == str(IngestErrorCode.ARCHIVE_FAILED)


@pytest.mark.asyncio
async def test_a_broken_api_does_not_take_the_worker_down(worker, django_api):
    """The pod would restart into the same broken API and stop draining."""
    django_api.claim_ingest_job.side_effect = RuntimeError("django-api is down")

    async def stop_soon():
        await asyncio.sleep(0.05)
        worker.drain()

    await asyncio.gather(worker.run(), stop_soon())

    assert django_api.claim_ingest_job.await_count >= 1


@pytest.mark.asyncio
async def test_draining_stops_the_loop(worker, django_api):
    async def stop_soon():
        await asyncio.sleep(0.05)
        worker.drain()

    await asyncio.wait_for(asyncio.gather(worker.run(), stop_soon()), timeout=5)

    assert worker.draining


@pytest.mark.asyncio
async def test_draining_wakes_the_worker_immediately(worker, django_api):
    """Waiting out a poll interval before noticing would make every scale-down
    as slow as the interval."""
    import asyncio

    worker.poll_interval_s = 300

    async def stop_soon():
        await asyncio.sleep(0.05)
        worker.drain()

    await asyncio.wait_for(asyncio.gather(worker.run(), stop_soon()), timeout=5)


@pytest.mark.asyncio
async def test_the_original_is_never_republished(worker, django_api, archive_root, color_bars_video):
    """The source is read, not rewritten; put() would refuse anyway."""
    original = archive_root / VIDEO_ID / "original" / "source.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(color_bars_video.read_bytes())
    before = original.stat().st_mtime_ns

    django_api.claim_ingest_job.side_effect = [job(), None]
    django_api.get_files_for_video.return_value = files(
        file_row(1, "original", f"{VIDEO_ID}/original/source.mp4", revision=0)
    )

    await worker.run_once()

    assert original.stat().st_mtime_ns == before
    registered = {c.kwargs["file_format"] for c in django_api.create_video_file.await_args_list}
    assert all(str(f) != "original" for f in registered)


@pytest.mark.asyncio
async def test_rebuilt_files_carry_the_current_revision(worker, django_api, archive_root, color_bars_video):
    (archive_root / VIDEO_ID / "original").mkdir(parents=True)
    (archive_root / VIDEO_ID / "original" / "source.mp4").write_bytes(color_bars_video.read_bytes())

    django_api.claim_ingest_job.side_effect = [job(), None]
    django_api.get_files_for_video.return_value = files(
        file_row(1, "original", f"{VIDEO_ID}/original/source.mp4", revision=0)
    )

    await worker.run_once()

    revisions = {c.kwargs["profile_revision"] for c in django_api.create_video_file.await_args_list}
    assert revisions == {1}


@pytest.mark.asyncio
async def test_the_source_is_left_where_the_archive_has_it(worker, django_api, archive_root, color_bars_video):
    """A backfill must not delete the original the way an upload deletes the
    file tusd left behind."""
    original = archive_root / VIDEO_ID / "original" / "source.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(color_bars_video.read_bytes())

    django_api.claim_ingest_job.side_effect = [job(), None]
    django_api.get_files_for_video.return_value = files(
        file_row(1, "original", f"{VIDEO_ID}/original/source.mp4", revision=0)
    )

    await worker.run_once()

    assert original.exists()
    assert PurePosixPath(f"{VIDEO_ID}/original/source.mp4")
