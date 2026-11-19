"""Archiving a recording MediaMTX made on request."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.recordings import routes


class FakeDjango:
    def __init__(self, state="importing"):
        self.calls: list[tuple] = []
        self.recording = {
            "id": 7,
            "video": 628900,
            "source": 38,
            "state": state,
            "playbackUrl": "http://recording:t@mediamtx-playback:9996/get?path=contrib/record/7",
        }

    async def __call__(self, django_api, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        return SimpleNamespace(json=lambda: self.recording)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    django = FakeDjango()
    monkeypatch.setattr(routes, "_django", django)

    async def fetch(url, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"mp4")

    monkeypatch.setattr(routes, "fetch", fetch)
    ingester = MagicMock()
    ingester.return_value.ingest = AsyncMock()
    monkeypatch.setattr(routes, "Ingester", ingester)
    reporter = MagicMock()
    reporter.return_value.state = AsyncMock()
    reporter.return_value.failed = AsyncMock()
    reporter.return_value.failed_unless_already = AsyncMock()
    monkeypatch.setattr(routes, "IngestReporter", reporter)
    extractor = SimpleNamespace(assert_compliance=AsyncMock(return_value="metadata"))
    settings = SimpleNamespace(tusd_dir=tmp_path)
    return django, ingester, extractor, settings, tmp_path


def run(setup):
    django, ingester, extractor, settings, _ = setup
    asyncio.run(routes.archive_recording(7, settings, MagicMock(), MagicMock(), extractor))


def test_fetches_the_window_and_ingests_it_like_an_upload(setup) -> None:
    django, ingester, extractor, settings, tmp_path = setup
    run(setup)
    target = tmp_path / "628900" / "recording-7.mp4"
    ingester.return_value.ingest.assert_awaited_once_with(
        "628900", target, "metadata", routes.IngestReporter.return_value
    )
    assert django.calls == [
        ("GET", "/api/recordings/7", None),
        ("POST", "/api/recordings/7/result", {"ok": True, "error": ""}),
    ]


def test_reports_a_failure(setup) -> None:
    django, ingester, extractor, settings, tmp_path = setup
    ingester.return_value.ingest.side_effect = RuntimeError("archive unreachable")
    run(setup)
    assert django.calls[-1] == ("POST", "/api/recordings/7/result", {"ok": False, "error": "archive unreachable"})
    assert not (tmp_path / "628900" / "recording-7.mp4").exists()


def test_does_nothing_for_a_request_not_waiting_on_ingest(setup) -> None:
    django, ingester, *_ = setup
    django.recording["state"] = "archived"
    run(setup)
    ingester.return_value.ingest.assert_not_awaited()
    assert django.calls == [("GET", "/api/recordings/7", None)]
