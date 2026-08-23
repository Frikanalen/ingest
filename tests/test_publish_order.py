"""What order a format's files reach the archive in.

The archive is exported read-only to the playout hosts, so the window between
the first and last file of a format landing is a window in which somebody can
read it. A manifest that arrives before the media it names is readable and
broken, which is worse than one that has not arrived at all.
"""

from pathlib import Path, PurePosixPath

import pytest

from app.archive_store import ArchiveEntry, ArchiveSession
from app.django_client.service import FormatEnum
from app.ingest import Ingester

VIDEO_ID = "12345"


class RecordingSession(ArchiveSession):
    """An archive that only remembers the order things were published in.

    The read side is stubbed rather than implemented: publishing never reads,
    and a double that pretended to would only invite a test to depend on
    something the real code path does not do.
    """

    def __init__(self):
        self.puts: list[PurePosixPath] = []

    async def exists(self, destination: PurePosixPath) -> bool:
        return False

    async def put(self, source: Path, destination: PurePosixPath) -> None:
        self.puts.append(destination)

    async def list_dir(self, destination: PurePosixPath) -> list[ArchiveEntry]:
        raise NotImplementedError

    async def get(self, source: PurePosixPath, destination: Path) -> None:
        raise NotImplementedError

    async def move(self, source: PurePosixPath, destination: PurePosixPath) -> None:
        raise NotImplementedError


@pytest.fixture
def ingester() -> Ingester:
    from unittest.mock import AsyncMock

    return Ingester(archive=AsyncMock(), django_api=AsyncMock())


@pytest.fixture
def dash_output(tmp_path) -> Path:
    output_dir = tmp_path / "dash"
    output_dir.mkdir()
    for name in ("manifest.mpd", "manifest-stream0.mp4", "manifest-stream1.mp4", "manifest-stream2.mp4"):
        (output_dir / name).write_bytes(b"x")
    return output_dir


@pytest.mark.asyncio
async def test_publishes_the_manifest_after_the_media_it_references(dash_output, ingester):
    session = RecordingSession()

    await ingester._publish(session, dash_output, dash_output / "manifest.mpd", VIDEO_ID, FormatEnum.DASH)

    assert [p.name for p in session.puts][-1] == "manifest.mpd"
    assert len(session.puts) == 4


@pytest.mark.asyncio
async def test_publishes_every_file_under_the_format_directory(dash_output, ingester):
    session = RecordingSession()

    await ingester._publish(session, dash_output, dash_output / "manifest.mpd", VIDEO_ID, FormatEnum.DASH)

    assert sorted(str(p) for p in session.puts) == sorted(
        f"{VIDEO_ID}/dash/{name}"
        for name in ("manifest.mpd", "manifest-stream0.mp4", "manifest-stream1.mp4", "manifest-stream2.mp4")
    )


@pytest.mark.asyncio
async def test_refuses_to_archive_a_format_that_nested_its_output(dash_output, ingester):
    """Destinations are flattened to a basename, so nesting would archive to
    the wrong paths rather than fail. Better to say so."""
    (dash_output / "subdir").mkdir()
    session = RecordingSession()

    with pytest.raises(NotImplementedError):
        await ingester._publish(session, dash_output, dash_output / "manifest.mpd", VIDEO_ID, FormatEnum.DASH)

    assert session.puts == []
