"""What the uploader is told while their file is being ingested.

Before this existed, a failed ingest was logged and nothing else: the
browser had already called the upload a success, and tusd has nowhere to
put a complaint that arrives ten minutes later. These pin the reports
that now carry that news back.
"""

import shutil
from pathlib import PurePosixPath
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import IngestStateEnum

from app.api.hooks.metadata import MetadataExtractor
from app.archive_store import FileAlreadyArchived, SshArchiveStore
from app.ingest import Ingester
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.util.settings import SshArchiveSettings
from tests.utils.ssh_server import run_ssh_server

VIDEO_ID = "12345"

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


@pytest.fixture
def django_api():
    return AsyncMock()


@pytest.fixture
def reported(django_api):
    """The (state, fields) pairs reported, in the order they were sent."""

    def states():
        return [call.args[1] for call in django_api.report_ingest_state.await_args_list]

    return states


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def work_dir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
def uploaded_file(tmp_path, color_bars_video):
    upload = tmp_path / "upload"
    upload.mkdir()
    destination = upload / "example_video.mp4"
    shutil.copy(color_bars_video, destination)
    return destination


@pytest_asyncio.fixture
async def ssh_server(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()

    async with run_ssh_server(keys) as server:
        yield server


@pytest.fixture
def archive(ssh_server, archive_root) -> SshArchiveStore:
    return SshArchiveStore(
        SshArchiveSettings(
            host=ssh_server.host,
            port=ssh_server.port,
            username=ssh_server.username,
            dir=PurePosixPath(archive_root),
            private_key_file=ssh_server.client_key_file,
            known_hosts_file=ssh_server.known_hosts_file,
        )
    )


@pytest_asyncio.fixture
async def metadata(uploaded_file):
    return await MetadataExtractor().do_probe(uploaded_file)


@pytest.mark.asyncio
async def test_a_successful_ingest_walks_from_archiving_to_done(
    archive, django_api, work_dir, uploaded_file, metadata, reported
):
    await Ingester(archive=archive, django_api=django_api, work_dir=work_dir).ingest(VIDEO_ID, uploaded_file, metadata)

    assert reported()[0] == IngestStateEnum.ARCHIVING
    assert reported()[-1] == IngestStateEnum.DONE
    assert IngestStateEnum.TRANSCODING in reported()


@pytest.mark.asyncio
async def test_transcoding_progress_tracks_ffmpegs_own_position(archive, django_api, work_dir, uploaded_file, metadata):
    """Progress is no longer just a jump between finished formats: ffmpeg's
    -progress output moves it within a format too, so it climbs smoothly
    from 0 to 100 rather than only ever reading 0 or 50."""
    await Ingester(archive=archive, django_api=django_api, work_dir=work_dir).ingest(VIDEO_ID, uploaded_file, metadata)

    percentages = [
        call.kwargs["percentage_done"]
        for call in django_api.report_ingest_state.await_args_list
        if call.args[1] == IngestStateEnum.TRANSCODING
    ]

    assert percentages[0] == 0
    # The thumbnail is done as soon as it starts; it must not read as though
    # it were as costly as the video encode that follows it. DASH is the only
    # encode left now that webm_med is gone, so its own weight now makes up
    # most of the total -- one thumbnail's share of that total is bigger than
    # it used to be, but still well short of an equal-weighted quarter.
    assert percentages[1] < 20
    assert percentages[-1] == 100
    assert percentages == sorted(percentages), "progress must never appear to move backwards"


@pytest.mark.asyncio
async def test_a_failed_archive_is_reported_with_a_code(
    archive, archive_root, django_api, work_dir, uploaded_file, metadata
):
    occupied = archive_root / VIDEO_ID / "original" / "example_video.mp4"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"already archived")

    with pytest.raises(FileAlreadyArchived):
        await Ingester(archive=archive, django_api=django_api, work_dir=work_dir).ingest(
            VIDEO_ID, uploaded_file, metadata
        )

    last = django_api.report_ingest_state.await_args_list[-1]

    assert last.args[1] == IngestStateEnum.FAILED
    assert last.kwargs["error_code"] == IngestErrorCode.ARCHIVE_FAILED
    assert last.kwargs["status_text"], "the operator needs to know which file was in the way"


@pytest.mark.asyncio
async def test_a_report_that_cannot_be_delivered_does_not_stop_the_ingest(
    archive, django_api, work_dir, uploaded_file, metadata
):
    """django-api being down must cost us a status line, not the video."""
    django_api.report_ingest_state.side_effect = RuntimeError("django-api is down")

    await Ingester(archive=archive, django_api=django_api, work_dir=work_dir).ingest(VIDEO_ID, uploaded_file, metadata)

    django_api.set_video_proper_import.assert_awaited_once_with(VIDEO_ID, True)


@pytest.mark.asyncio
async def test_operator_detail_is_trimmed_to_what_the_column_holds(django_api):
    reporter = IngestReporter(django_api, VIDEO_ID)

    await reporter.failed(IngestErrorCode.TRANSCODE_FAILED, "x" * 5000)

    assert len(django_api.report_ingest_state.await_args.kwargs["status_text"]) == 1000


@pytest.mark.asyncio
async def test_the_backstop_does_not_overwrite_a_specific_failure(django_api):
    reporter = IngestReporter(django_api, VIDEO_ID)

    await reporter.failed(IngestErrorCode.NOT_COMPLIANT, "no video stream")
    await reporter.failed_unless_already(IngestErrorCode.INTERNAL_ERROR, "something else")

    assert django_api.report_ingest_state.await_count == 1
    assert django_api.report_ingest_state.await_args.kwargs["error_code"] == IngestErrorCode.NOT_COMPLIANT


@pytest.mark.asyncio
async def test_the_backstop_reports_when_nothing_else_did(django_api):
    reporter = IngestReporter(django_api, VIDEO_ID)

    await reporter.failed_unless_already(IngestErrorCode.INTERNAL_ERROR, "boom")

    assert django_api.report_ingest_state.await_args.kwargs["error_code"] == IngestErrorCode.INTERNAL_ERROR
