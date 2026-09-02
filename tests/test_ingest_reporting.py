"""What the uploader is told while their file is being ingested.

Before this existed, a failed ingest was logged and nothing else: the
browser had already called the upload a success, and tusd has nowhere to
put a complaint that arrives ten minutes later. These pin the reports
that now carry that news back.
"""

import shutil
from pathlib import PurePosixPath

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import IngestStateEnum

from app.api.hooks.metadata import MetadataExtractor
from app.archive_store import SshArchiveStore
from app.archive_store.ssh import SshArchiveSession
from app.ingest import Ingester
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.util.settings import SshArchiveSettings
from tests.utils.catalogue import recording_django_api
from tests.utils.drain import drain_one
from tests.utils.ssh_server import run_ssh_server

VIDEO_ID = "12345"

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


@pytest.fixture
def django_api():
    return recording_django_api(VIDEO_ID)


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
    """The walk now spans both halves, and the member sees one sequence
    regardless of which process wrote each step."""
    await Ingester(archive=archive, django_api=django_api).ingest(VIDEO_ID, uploaded_file, metadata)
    await drain_one(archive, django_api, work_dir)

    assert reported()[0] == IngestStateEnum.ARCHIVING
    assert reported()[-1] == IngestStateEnum.DONE
    assert IngestStateEnum.TRANSCODING in reported()


@pytest.mark.asyncio
async def test_the_hook_leaves_the_video_queued_rather_than_done(
    archive, django_api, work_dir, uploaded_file, metadata, reported
):
    """Between the hook returning and a worker claiming, the honest answer is
    `pending`. Reporting DONE here would tell a member their video was ready
    when nothing had been built yet."""
    await Ingester(archive=archive, django_api=django_api).ingest(VIDEO_ID, uploaded_file, metadata)

    assert reported()[-1] == IngestStateEnum.ARCHIVING
    assert IngestStateEnum.DONE not in reported()

    queued = await django_api.get_ingest_job(VIDEO_ID)
    assert queued.state == IngestStateEnum.PENDING


@pytest.mark.asyncio
async def test_transcoding_progress_tracks_dashs_own_position(archive, django_api, work_dir, uploaded_file, metadata):
    """Thumbnails carry no percentage of their own: they finish before ffmpeg
    would have anything to report, and measured against a 60s 1080p source
    the DASH ladder outweighs the other three formats combined by roughly
    100x. So there is no ladder of format weights to keep in step with
    reality any more -- entering TRANSCODING reports nothing until DASH's
    own -progress stream starts advancing it, straight to 100 at the end."""
    await Ingester(archive=archive, django_api=django_api).ingest(VIDEO_ID, uploaded_file, metadata)
    await drain_one(archive, django_api, work_dir)

    percentages = [
        call.kwargs["percentage_done"]
        for call in django_api.report_ingest_state.await_args_list
        if call.args[1] == IngestStateEnum.TRANSCODING
    ]

    assert percentages[0] is None, "nothing to report until DASH has started"
    assert percentages[-1] == 100
    known = [p for p in percentages if p is not None]
    assert known == sorted(known), "progress must never appear to move backwards"


@pytest.mark.asyncio
async def test_a_failed_archive_is_reported_with_a_code(
    archive, archive_root, django_api, work_dir, uploaded_file, metadata, monkeypatch
):
    """An occupied destination used to be how this failure was provoked. It is
    a supported case now -- an upload supersedes what was there -- so the
    archive is made to refuse outright instead."""

    async def refuse(self, source, destination):
        raise RuntimeError("no space left on device")

    monkeypatch.setattr(SshArchiveSession, "put", refuse)

    with pytest.raises(RuntimeError, match="no space left on device"):
        await Ingester(archive=archive, django_api=django_api).ingest(VIDEO_ID, uploaded_file, metadata)

    last = django_api.report_ingest_state.await_args_list[-1]

    assert last.args[1] == IngestStateEnum.FAILED
    assert last.kwargs["error_code"] == IngestErrorCode.ARCHIVE_FAILED
    assert last.kwargs["status_text"], "the operator needs to know why the archive would not take it"


@pytest.mark.asyncio
async def test_a_report_that_cannot_be_delivered_does_not_stop_the_ingest(
    archive, django_api, work_dir, uploaded_file, metadata
):
    """django-api being down must cost us a status line, not the video."""
    django_api.report_ingest_state.side_effect = RuntimeError("django-api is down")

    await Ingester(archive=archive, django_api=django_api).ingest(VIDEO_ID, uploaded_file, metadata)
    await drain_one(archive, django_api, work_dir)

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
