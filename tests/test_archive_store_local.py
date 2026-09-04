from pathlib import PurePosixPath

import pytest

from app.archive_store import ArchiveError, FileAlreadyArchived, LocalArchiveStore, create_archive_store
from app.archive_store.local import SPOOL_DIR
from app.util.settings import LocalArchiveSettings

DESTINATION = PurePosixPath("12345/original/example_video.mp4")


@pytest.fixture
def source_file(tmp_path):
    source = tmp_path / "example_video.mp4"
    source.write_bytes(b"video payload")
    return source


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.mark.asyncio
async def test_put_creates_missing_parents(archive_root, source_file):
    async with LocalArchiveStore(archive_root).open() as archive:
        await archive.put(source_file, DESTINATION)

    assert (archive_root / DESTINATION).read_bytes() == b"video payload"


@pytest.mark.asyncio
async def test_put_leaves_no_partial_file_behind(archive_root, source_file):
    async with LocalArchiveStore(archive_root).open() as archive:
        await archive.put(source_file, DESTINATION)

    assert sorted(p.name for p in (archive_root / DESTINATION).parent.iterdir()) == ["example_video.mp4"]
    assert not list((archive_root / SPOOL_DIR).rglob("*.mp4"))


@pytest.mark.asyncio
async def test_put_keeps_the_source(archive_root, source_file):
    """Derivatives are generated from the upload, so archiving must not consume it."""
    async with LocalArchiveStore(archive_root).open() as archive:
        await archive.put(source_file, DESTINATION)

    assert source_file.exists()


@pytest.mark.asyncio
async def test_exists_reports_archived_files(archive_root, source_file):
    async with LocalArchiveStore(archive_root).open() as archive:
        assert not await archive.exists(DESTINATION)
        await archive.put(source_file, DESTINATION)
        assert await archive.exists(DESTINATION)


@pytest.mark.asyncio
async def test_assert_absent_rejects_an_occupied_destination(archive_root, source_file):
    async with LocalArchiveStore(archive_root).open() as archive:
        await archive.put(source_file, DESTINATION)

        with pytest.raises(FileAlreadyArchived):
            await archive.assert_absent(DESTINATION)


def test_missing_archive_directory_is_rejected_on_construction(tmp_path):
    with pytest.raises(ArchiveError):
        LocalArchiveStore(tmp_path / "nonexistent")


def test_local_settings_select_the_local_store(archive_root):
    store = create_archive_store(LocalArchiveSettings(dir=archive_root))

    assert isinstance(store, LocalArchiveStore)
