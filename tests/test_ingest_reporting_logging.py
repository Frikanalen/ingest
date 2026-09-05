import logging
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import IngestStateEnum

from app.ingest_reporting import IngestReporter, transcode_progress_reporter

VIDEO_ID = "12345"


@pytest.mark.asyncio
async def test_each_percentage_update_is_logged(caplog):
    reporter = IngestReporter(AsyncMock(), VIDEO_ID)
    report_progress = transcode_progress_reporter(reporter)

    with caplog.at_level(logging.INFO, logger="app.ingest_reporting"):
        await report_progress(0.104)
        await report_progress(0.105)
        await report_progress(0.114)

    progress_messages = [record.message for record in caplog.records if "Updated ingest progress" in record.message]
    assert progress_messages == [
        "Updated ingest progress for video 12345 to 10% (transcoding)",
        "Updated ingest progress for video 12345 to 11% (transcoding)",
    ]


@pytest.mark.asyncio
async def test_a_failed_percentage_update_is_not_logged_as_updated(caplog):
    django_api = AsyncMock()
    django_api.report_ingest_state.side_effect = RuntimeError("django-api is down")
    reporter = IngestReporter(django_api, VIDEO_ID)

    with caplog.at_level(logging.INFO, logger="app.ingest_reporting"):
        await reporter.state(IngestStateEnum.TRANSCODING, percentage_done=42)

    assert "Updated ingest progress" not in caplog.text
    assert "Could not report ingest state" in caplog.text
