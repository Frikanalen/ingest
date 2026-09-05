"""Draining the queue.

The loop itself is small; what these hold in place is what it does when things
go wrong. A worker that dies on a bad video stops draining the queue, and a
worker that abandons a claim the moment it is asked to stop throws away however
many hours have gone into it.
"""

import asyncio
import signal
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import IngestStateEnum, VideoFileVariantEnum

from app.archive_store import LocalArchiveStore
from app.converge.apply import SourceUnavailable
from app.ingest_reporting import IngestErrorCode
from app.media.produce import TranscodeFailed
from app.worker import Worker

VIDEO_ID = "12345"


def job(video=int(VIDEO_ID), kind="backfill"):
    return SimpleNamespace(video=video, kind=kind)


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
async def test_a_pool_that_names_a_queue_asks_only_for_that_queue(worker, django_api):
    """A pool pinned to one kind is how you keep member uploads out of a lane
    that is busy with a catalogue-wide re-encode."""
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
async def test_a_job_claimed_while_draining_is_left_to_its_lease(worker, django_api):
    """SIGTERM can land after the loop has decided to ask and before the claim
    comes back. Starting hours of work with only the grace period left to do it
    in is how an encode gets killed partway, which is the one way to lose it."""

    async def claim_then_drain(*args, **kwargs):
        worker.drain()
        return job()

    django_api.claim_ingest_job.side_effect = claim_then_drain

    assert await worker.run_once() is True

    django_api.get_files_for_video.assert_not_awaited()
    django_api.release_ingest_job.assert_awaited_once_with(VIDEO_ID)


@pytest.mark.asyncio
async def test_a_claim_that_cannot_be_handed_back_still_exits(worker, django_api):
    """The lease is the backstop and expires either way; failing the handover
    is no reason to turn a tidy exit into a failed one."""

    async def claim_then_drain(*args, **kwargs):
        worker.drain()
        return job()

    django_api.claim_ingest_job.side_effect = claim_then_drain
    django_api.release_ingest_job.side_effect = RuntimeError("django-api is down")

    assert await worker.run_once() is True

    django_api.get_files_for_video.assert_not_awaited()


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
    """The archived original is the source for every rebuild after this one, so
    a worker reads it and never removes it."""
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


@pytest.mark.asyncio
async def test_an_upload_is_serviced_like_anything_else(worker, django_api):
    """Kind says who is waiting, not what the worker can reach. Once the hook
    has archived the original there is nothing about an upload a worker cannot
    do, so refusing one would be refusing work it is perfectly able to finish."""
    django_api.claim_ingest_job.side_effect = [job(kind="upload"), None]

    assert await worker.run_once() is True

    assert reported_states(django_api)[-1] == IngestStateEnum.DONE
    assert IngestStateEnum.FAILED not in reported_states(django_api)


@pytest.mark.asyncio
async def test_an_upload_is_marked_importable_when_it_finishes(worker, django_api):
    """The flag means "this video is everything it was supposed to be", and the
    worker is the one that knows -- the hook returned long before any format
    existed."""
    django_api.claim_ingest_job.side_effect = [job(kind="upload"), None]

    await worker.run_once()

    django_api.set_video_proper_import.assert_awaited_once_with(VIDEO_ID, True)


@pytest.mark.asyncio
async def test_a_backfill_does_not_mark_anything_importable(worker, django_api):
    """A backfill promises only that its plan ran. Flipping the flag on a
    legacy video would publish something the catalogue is currently hiding,
    which is an editorial decision and not one a reconciler gets to make."""
    django_api.claim_ingest_job.side_effect = [job(kind="backfill"), None]

    await worker.run_once()

    django_api.set_video_proper_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_upload_is_not_marked_importable(worker, django_api, archive_root):
    """Nothing was built, so nothing may be published."""
    django_api.claim_ingest_job.side_effect = [job(kind="upload"), None]
    django_api.get_files_for_video.return_value = files(
        file_row(1, "original", f"{VIDEO_ID}/original/missing.mp4", revision=0)
    )
    (archive_root / VIDEO_ID / "original").mkdir(parents=True)

    await worker.run_once()

    django_api.set_video_proper_import.assert_not_awaited()
    assert reported_states(django_api)[-1] == IngestStateEnum.FAILED


@pytest.mark.asyncio
async def test_a_signal_during_startup_is_not_lost(worker, monkeypatch):
    """The container is PID 1, and the kernel drops a signal PID 1 has no
    handler for. A worker deleted a second after it was created -- which a
    rollout does -- would otherwise never hear its only SIGTERM, and spend the
    whole hour-long grace period claiming jobs nobody expects it to take."""
    import app.worker as worker_module

    monkeypatch.setattr(worker_module, "_signalled_during_startup", True)
    worker_module.install_drain_handlers(worker)

    assert worker.draining
    await asyncio.wait_for(worker.run(), timeout=5)


@pytest.mark.asyncio
async def test_the_startup_handler_records_the_signal(monkeypatch):
    import app.worker as worker_module

    monkeypatch.setattr(worker_module, "_signalled_during_startup", False)
    worker_module._note_signal_during_startup(signal.SIGTERM, None)

    assert worker_module._signalled_during_startup
