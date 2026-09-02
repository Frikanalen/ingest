"""Putting videos in the queue.

An ingest job is keyed on its video, so enqueueing replaces whatever is there.
The only thing that must not happen is replacing a job somebody is working: a
member watching their upload would see it go back to the beginning, and a
second worker could be invited onto a video whose original is mid-archive.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import IngestKindEnum, IngestStateEnum

from app.backfill.enqueue import Enqueuer


def job(state):
    return SimpleNamespace(state=state)


@pytest.fixture
def django_api():
    api = AsyncMock()
    api.get_ingest_job.return_value = job(IngestStateEnum.DONE)
    return api


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [IngestStateEnum.DONE, IngestStateEnum.FAILED, IngestStateEnum.PENDING])
async def test_a_video_not_being_worked_is_queued(django_api, state):
    django_api.get_ingest_job.return_value = job(state)

    report = await Enqueuer(django_api).enqueue_all(["12345"])

    assert report.enqueued == ["12345"]
    django_api.enqueue_ingest_job.assert_awaited_once_with("12345", kind="backfill", priority=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [IngestStateEnum.PROBING, IngestStateEnum.ARCHIVING, IngestStateEnum.TRANSCODING])
async def test_a_video_being_worked_is_left_alone(django_api, state):
    """Overwriting this would reset someone's upload under them."""
    django_api.get_ingest_job.return_value = job(state)

    report = await Enqueuer(django_api).enqueue_all(["12345"])

    assert report.already_running == ["12345"]
    django_api.enqueue_ingest_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_kind_is_always_sent(django_api):
    """A row that does not exist yet defaults to `upload`, which would leave
    the job claimable only by the pod that cannot reach the archive."""
    await Enqueuer(django_api).enqueue_all(["12345"])

    assert django_api.enqueue_ingest_job.await_args.kwargs["kind"] == "backfill"


@pytest.mark.asyncio
async def test_priority_is_carried_through(django_api):
    await Enqueuer(django_api, priority=50).enqueue_all(["12345"])

    assert django_api.enqueue_ingest_job.await_args.kwargs["priority"] == 50


@pytest.mark.asyncio
async def test_one_failure_does_not_lose_the_rest(django_api):
    """A backfill queues thousands; one bad row must not abort the run."""

    async def sometimes_broken(video_id):
        if video_id == "2":
            raise RuntimeError("django-api said no")
        return job(IngestStateEnum.DONE)

    django_api.get_ingest_job.side_effect = sometimes_broken

    report = await Enqueuer(django_api).enqueue_all(["1", "2", "3"])

    assert report.enqueued == ["1", "3"]
    assert "django-api said no" in report.failed["2"]


@pytest.mark.asyncio
async def test_a_generator_of_ids_is_not_silently_dropped(django_api):
    """The caller's ids are usually a generator over a plan, and the results
    are matched back against them -- so they have to survive being walked."""
    report = await Enqueuer(django_api).enqueue_all(str(i) for i in range(3))

    assert sorted(report.enqueued) == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_reading_the_state_never_creates_a_row(django_api):
    """django-api answers `pending` from an unsaved row for a video nothing has
    reported on, so a check must not be what puts it in the queue."""
    django_api.get_ingest_job.return_value = job(IngestStateEnum.PENDING)

    await Enqueuer(django_api).enqueue_all(["12345"])

    assert django_api.get_ingest_job.await_count == 1
    assert django_api.enqueue_ingest_job.await_count == 1


@pytest.mark.asyncio
async def test_a_queued_upload_is_left_alone(django_api):
    """A member's upload waiting to be claimed is not idle work to be swept up.

    PUTting over it would drop it to backfill priority behind everything else
    and take away the kind the completion step keys on, so the video would
    finish without ever being marked importable.
    """
    django_api.get_ingest_job.return_value = SimpleNamespace(
        state=IngestStateEnum.PENDING, kind=IngestKindEnum.UPLOAD
    )

    report = await Enqueuer(django_api).enqueue_all(["12345"])

    assert report.already_running == ["12345"]
    django_api.enqueue_ingest_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_queued_backfill_is_replaced(django_api):
    """Re-running `apply` is how you resume, so a waiting backfill is ours to
    re-queue -- only an upload is somebody else's."""
    django_api.get_ingest_job.return_value = SimpleNamespace(
        state=IngestStateEnum.PENDING, kind=IngestKindEnum.BACKFILL
    )

    report = await Enqueuer(django_api).enqueue_all(["12345"])

    assert report.enqueued == ["12345"]
