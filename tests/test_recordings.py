"""Archiving a recording MediaMTX made on request."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
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
            "starttime": "2026-11-25T14:00:00+01:00",
            "endtime": "2026-11-25T15:00:00+01:00",
            "playbackUrl": (
                "http://recording:t@mediamtx-playback:9996/get?path=contrib/record/7"
                "&start=2026-11-25T14:00:00%2B01:00&duration=3600&format=fmp4"
            ),
        }

    async def __call__(self, django_api, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        return SimpleNamespace(json=lambda: self.recording)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    django = FakeDjango()
    monkeypatch.setattr(routes, "_django", django)

    async def list_runs(url):
        return [("run-a", 1800.0), ("run-b", 1800.0)]

    monkeypatch.setattr(routes, "list_runs", list_runs)

    async def fetch(url, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(url.encode())

    monkeypatch.setattr(routes, "fetch", fetch)

    async def remux(sources: list[Path], target: Path):
        target.write_bytes(b"+".join(source.read_bytes() for source in sources))

    monkeypatch.setattr(routes, "remux", remux)
    monkeypatch.setattr(routes, "duration", AsyncMock(return_value=3599.0))
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
    assert target.read_bytes() == b"run-a+run-b"
    assert sorted(p.name for p in target.parent.iterdir()) == ["recording-7.mp4"]
    ingester.return_value.ingest.assert_awaited_once_with(
        "628900", target, "metadata", routes.IngestReporter.return_value
    )
    assert django.calls == [
        ("GET", "/api/recordings/7", None),
        ("POST", "/api/recordings/7/result", {"ok": True, "error": ""}),
    ]


def test_fetches_the_master_when_django_names_one(setup) -> None:
    django, ingester, extractor, settings, tmp_path = setup
    django.recording["masterUrl"] = "https://mpeg.democracynow.org/dn2026-1125-hd.mp4"
    run(setup)
    target = tmp_path / "628900" / "recording-7.mp4"
    assert target.read_bytes() == b"https://mpeg.democracynow.org/dn2026-1125-hd.mp4"
    ingester.return_value.ingest.assert_awaited_once()
    assert django.calls[-1][2] == {"ok": True, "error": ""}


def test_a_short_master_fails(setup, monkeypatch) -> None:
    django, ingester, *_ = setup
    django.recording["masterUrl"] = "https://mpeg.democracynow.org/dn2026-1125-hd.mp4"
    monkeypatch.setattr(routes, "duration", AsyncMock(return_value=600.0))
    run(setup)
    ingester.return_value.ingest.assert_not_awaited()
    assert django.calls[-1][2] == {"ok": False, "error": "The master is only 600 s of a 3600 s window"}


def test_reports_a_failure(setup) -> None:
    django, ingester, extractor, settings, tmp_path = setup
    ingester.return_value.ingest.side_effect = RuntimeError("archive unreachable")
    run(setup)
    assert django.calls[-1] == ("POST", "/api/recordings/7/result", {"ok": False, "error": "archive unreachable"})
    assert not (tmp_path / "628900" / "recording-7.mp4").exists()


def test_fails_when_the_archive_is_much_shorter_than_the_recording(setup, monkeypatch) -> None:
    django, ingester, *_ = setup
    monkeypatch.setattr(routes, "duration", AsyncMock(return_value=178.6))
    run(setup)
    ingester.return_value.ingest.assert_not_awaited()
    assert django.calls[-1] == (
        "POST", "/api/recordings/7/result", {"ok": False, "error": "Archived only 179 s of 3600 s recorded"},
    )  # fmt: skip


def test_fails_when_nothing_was_recorded(setup, monkeypatch) -> None:
    django, ingester, *_ = setup

    async def nothing(url):
        return []

    monkeypatch.setattr(routes, "list_runs", nothing)
    run(setup)
    ingester.return_value.ingest.assert_not_awaited()
    assert django.calls[-1][2] == {"ok": False, "error": "Nothing was recorded in the window"}


def test_lists_the_unbroken_runs_clipped_to_the_window(monkeypatch) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"start": "2026-11-25T12:59:09Z", "duration": 108.0, "url": "ignored"},
                {"start": "2026-11-25T13:00:57Z", "duration": 176.0, "url": "ignored"},
                {"start": "2026-11-25T13:59:00Z", "duration": 300.0, "url": "ignored"},
            ],
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    url = (
        "http://recording:t@mediamtx-playback:9996/get?path=contrib/record/1"
        "&start=2026-11-25T14:00:00%2B01:00&duration=3600&format=fmp4"
    )
    runs = asyncio.run(routes.list_runs(url))
    listed = seen[0].url
    assert listed.path == "/list"
    assert dict(listed.params) == {
        "path": "contrib/record/1", "start": "2026-11-25T14:00:00+01:00", "end": "2026-11-25T15:00:00+01:00",
    }  # fmt: skip
    assert seen[0].headers["authorization"] == "Basic cmVjb3JkaW5nOnQ="  # recording:t
    assert "recording:t@" not in str(listed)
    assert [seconds for _, seconds in runs] == [57.0, 176.0, 60.0]
    first = httpx.URL(runs[0][0])
    assert (first.path, first.username, first.params["format"]) == ("/get", "recording", "fmp4")
    assert first.params["start"] == "2026-11-25T14:00:00+01:00"
    assert first.params["duration"] == "57"


def test_does_nothing_for_a_request_not_waiting_on_ingest(setup) -> None:
    django, ingester, *_ = setup
    django.recording["state"] = "archived"
    run(setup)
    ingester.return_value.ingest.assert_not_awaited()
    assert django.calls == [("GET", "/api/recordings/7", None)]


def test_remuxes_fragmented_mp4_to_plain(tmp_path) -> None:
    import subprocess

    source, target = tmp_path / "in.fmp4", tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=320x240:r=25",
         "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-g", "25", "-c:a", "aac",
         "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", str(source)],
        check=True,
    )  # fmt: skip
    asyncio.run(routes.remux([source], target))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(target)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    assert abs(float(probe.stdout) - 2.0) < 0.2
    assert b"moof" not in target.read_bytes()[:200000]


def test_joins_runs_into_one_plain_mp4(tmp_path) -> None:
    import subprocess

    sources = [tmp_path / "a.fmp4", tmp_path / "b.fmp4"]
    for source in sources:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=320x240:r=25",
             "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-g", "25", "-c:a", "aac",
             "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", str(source)],
            check=True,
        )  # fmt: skip
    target = tmp_path / "out.mp4"
    asyncio.run(routes.remux(sources, target))
    assert abs(asyncio.run(routes.duration(target)) - 4.0) < 0.3
    assert not (tmp_path / "out.txt").exists()
