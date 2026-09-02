"""What both archives must agree on.

The two backends are meant to be interchangeable, and until now they quietly
were not: publishing over an existing file overwrote it locally and failed over
SFTP, so a backfill -- which republishes by definition -- would have passed in
development and failed against file01. Every test here runs against both stores
so that a divergence like that shows up as a failure rather than as a surprise
in production.
"""

from pathlib import PurePosixPath

import pytest
import pytest_asyncio

from app.archive_store import FileAlreadyArchived, LocalArchiveStore, SshArchiveStore
from app.archive_store.base import TRASH_DIR
from app.util.settings import SshArchiveSettings
from tests.utils.ssh_server import run_ssh_server

DESTINATION = PurePosixPath("12345/original/example_video.mp4")
PAYLOAD = b"video payload"


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def source_file(tmp_path):
    source = tmp_path / "example_video.mp4"
    source.write_bytes(PAYLOAD)
    return source


@pytest_asyncio.fixture(params=["local", "ssh"])
async def store(request, tmp_path, archive_root):
    """The same archive, reached the two ways ingest can reach one."""
    if request.param == "local":
        yield LocalArchiveStore(archive_root)
        return

    keys = tmp_path / "keys"
    keys.mkdir()
    async with run_ssh_server(keys) as server:
        yield SshArchiveStore(
            SshArchiveSettings(
                host=server.host,
                port=server.port,
                username=server.username,
                dir=PurePosixPath(archive_root),
                private_key_file=server.client_key_file,
                known_hosts_file=server.known_hosts_file,
            )
        )


@pytest.mark.asyncio
async def test_put_refuses_to_replace_an_existing_file(store, archive_root, source_file):
    """Republishing is never an overwrite: a rebuild swaps the directory instead.

    A new profile revision can emit a different set of files, so overwriting one
    by one would leave the previous profile's leftovers beside the new output.
    """
    target = archive_root / DESTINATION
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone got here first")

    async with store.open() as archive:
        with pytest.raises(FileAlreadyArchived):
            await archive.put(source_file, DESTINATION)

    assert target.read_bytes() == b"someone got here first"


@pytest.mark.asyncio
async def test_list_dir_reports_names_types_and_sizes(store, archive_root, source_file):
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)
        await archive.put(source_file, PurePosixPath("12345/large_thumb/example_video.jpg"))

        video = await archive.list_dir(PurePosixPath("12345"))
        assert [entry.name for entry in video] == ["large_thumb", "original"]
        assert all(entry.is_dir for entry in video)

        [original] = await archive.list_dir(PurePosixPath("12345/original"))
        assert original.path == DESTINATION
        assert not original.is_dir
        assert original.size == len(PAYLOAD)


@pytest.mark.asyncio
async def test_list_dir_of_a_missing_directory_is_empty(store):
    """Callers are asking what is there, and "nothing" is an answer to that."""
    async with store.open() as archive:
        assert await archive.list_dir(PurePosixPath("99999/dash")) == []


@pytest.mark.asyncio
async def test_list_dir_finds_the_videos_at_the_archive_root(store, source_file):
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)

        entries = await archive.list_dir(PurePosixPath("."))
        assert [entry.path for entry in entries if not entry.name.startswith(".")] == [PurePosixPath("12345")]


@pytest.mark.asyncio
async def test_get_retrieves_an_archived_file(store, tmp_path, source_file):
    destination = tmp_path / "work" / "retrieved.mp4"

    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)
        await archive.get(DESTINATION, destination)

    assert destination.read_bytes() == PAYLOAD


@pytest.mark.asyncio
async def test_get_leaves_no_partial_file_behind(store, tmp_path, source_file):
    destination = tmp_path / "work" / "retrieved.mp4"

    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)
        await archive.get(DESTINATION, destination)

    assert sorted(p.name for p in destination.parent.iterdir()) == ["retrieved.mp4"]


@pytest.mark.asyncio
async def test_get_of_a_missing_file_raises_file_not_found(store, tmp_path):
    """A row pointing at nothing is a condition to report, not an SFTP detail."""
    async with store.open() as archive:
        with pytest.raises(FileNotFoundError):
            await archive.get(PurePosixPath("99999/original/gone.mp4"), tmp_path / "gone.mp4")


@pytest.mark.asyncio
async def test_move_renames_and_creates_missing_parents(store, archive_root, source_file):
    broadcast = PurePosixPath("12345/broadcast/example_video.mp4")

    async with store.open() as archive:
        await archive.put(source_file, broadcast)
        await archive.move(broadcast, DESTINATION)

    assert (archive_root / DESTINATION).read_bytes() == PAYLOAD
    assert not (archive_root / broadcast).exists()


@pytest.mark.asyncio
async def test_move_refuses_to_replace_an_existing_destination(store, archive_root, source_file):
    broadcast = PurePosixPath("12345/broadcast/example_video.mp4")

    async with store.open() as archive:
        await archive.put(source_file, broadcast)
        await archive.put(source_file, DESTINATION)

        with pytest.raises(FileAlreadyArchived):
            await archive.move(broadcast, DESTINATION)

    assert (archive_root / broadcast).exists()


@pytest.mark.asyncio
async def test_trash_takes_a_directory_out_of_the_published_tree(store, archive_root, source_file):
    broadcast_dir = PurePosixPath("12345/broadcast")

    async with store.open() as archive:
        await archive.put(source_file, broadcast_dir / "example_video.mp4")
        landed = await archive.trash(broadcast_dir)

    assert not (archive_root / broadcast_dir).exists()
    assert (archive_root / landed / "example_video.mp4").read_bytes() == PAYLOAD
    assert landed.parts[0] == TRASH_DIR.name


@pytest.mark.asyncio
async def test_trash_keeps_the_original_path_under_the_timestamp(store, source_file):
    """Putting something back should be a rename to where its name already says."""
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)
        landed = await archive.trash(DESTINATION)

    # .trash/<when>/12345/original/example_video.mp4
    assert PurePosixPath(*landed.parts[2:]) == DESTINATION


@pytest.mark.asyncio
async def test_trashing_is_not_deleting(store, archive_root, source_file):
    """Nothing in this codebase destroys archived media; purging is separate."""
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)
        await archive.trash(DESTINATION)

    assert [p for p in (archive_root / TRASH_DIR).rglob("*") if p.is_file()]


@pytest.mark.asyncio
async def test_a_video_directory_survives_a_round_trip(store, tmp_path, archive_root, source_file):
    """The shape a backfill actually uses: list, fetch, rebuild, swap."""
    async with store.open() as archive:
        await archive.put(source_file, PurePosixPath("12345/dash/manifest.mpd"))
        await archive.put(source_file, DESTINATION)

        original = tmp_path / "work" / "original.mp4"
        await archive.get(DESTINATION, original)
        assert original.read_bytes() == PAYLOAD

        await archive.trash(PurePosixPath("12345/dash"))
        rebuilt = tmp_path / "manifest.mpd"
        rebuilt.write_bytes(b"rebuilt manifest")
        await archive.put(rebuilt, PurePosixPath("12345/dash/manifest.mpd"))

    assert (archive_root / "12345/dash/manifest.mpd").read_bytes() == b"rebuilt manifest"
    assert (archive_root / DESTINATION).read_bytes() == PAYLOAD


def test_both_backends_were_exercised(store):
    """Guards the parametrization itself: a fixture that silently stopped
    yielding the SSH store would make every test above a local-only test."""
    assert isinstance(store, LocalArchiveStore | SshArchiveStore)
