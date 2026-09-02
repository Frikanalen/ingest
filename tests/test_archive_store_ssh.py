import getpass
import pwd
from pathlib import PurePosixPath

import asyncssh
import pytest
import pytest_asyncio

from app.archive_store import ArchiveError, FileAlreadyArchived, SshArchiveStore, create_archive_store
from app.archive_store.base import SPOOL_DIR, staging_path
from app.util.settings import SshArchiveSettings
from tests.utils.ssh_server import run_ssh_server

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


@pytest_asyncio.fixture
async def ssh_server(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()

    async with run_ssh_server(keys) as server:
        yield server


@pytest.fixture
def store(ssh_server, archive_root) -> SshArchiveStore:
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


@pytest.mark.asyncio
async def test_put_uploads_and_creates_missing_parents(store, archive_root, source_file):
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)

    assert (archive_root / DESTINATION).read_bytes() == b"video payload"


@pytest.mark.asyncio
async def test_put_leaves_no_partial_file_behind(store, archive_root, source_file):
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)

    assert sorted(p.name for p in (archive_root / DESTINATION).parent.iterdir()) == ["example_video.mp4"]
    assert not list((archive_root / SPOOL_DIR).rglob("*.mp4"))


@pytest.mark.asyncio
async def test_put_keeps_the_source(store, source_file):
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)

    assert source_file.exists()


@pytest.mark.asyncio
async def test_exists_reports_archived_files(store, source_file):
    async with store.open() as archive:
        assert not await archive.exists(DESTINATION)
        await archive.put(source_file, DESTINATION)
        assert await archive.exists(DESTINATION)


@pytest.mark.asyncio
async def test_assert_absent_rejects_an_occupied_destination(store, source_file):
    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)

        with pytest.raises(FileAlreadyArchived):
            await archive.assert_absent(DESTINATION)


@pytest.mark.asyncio
async def test_put_refuses_to_overwrite_a_file_that_appeared_mid_job(store, archive_root, source_file):
    """The publishing rename must fail rather than clobber a racing writer.

    Reported as FileAlreadyArchived rather than as whatever SFTP error the
    server chose, so callers can tell an occupied destination apart from a full
    disk without knowing anything about asyncssh.
    """
    target = archive_root / DESTINATION
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone got here first")

    async with store.open() as archive:
        with pytest.raises(FileAlreadyArchived):
            await archive.put(source_file, DESTINATION)

    assert target.read_bytes() == b"someone got here first"


@pytest.mark.asyncio
async def test_a_failed_publish_leaves_nothing_in_the_published_tree(store, archive_root, source_file):
    """A leftover transfer is parked in the spool, never beside the real file."""
    target = archive_root / DESTINATION
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone got here first")

    async with store.open() as archive:
        with pytest.raises(FileAlreadyArchived):
            await archive.put(source_file, DESTINATION)

    assert (archive_root / staging_path(DESTINATION)).exists()
    assert sorted(p.name for p in target.parent.iterdir()) == ["example_video.mp4"]


@pytest.mark.asyncio
async def test_multiple_files_share_one_connection(store, archive_root, tmp_path):
    """One session per ingest job carries the original and every derivative."""
    files = {
        PurePosixPath("12345/original/example_video.mp4"): b"original",
        PurePosixPath("12345/webm_med/example_video.webm"): b"webm",
        PurePosixPath("12345/large_thumb/example_video.jpg"): b"thumb",
    }

    async with store.open() as archive:
        for destination, payload in files.items():
            source = tmp_path / destination.name
            source.write_bytes(payload)
            await archive.put(source, destination)

    for destination, payload in files.items():
        assert (archive_root / destination).read_bytes() == payload


@pytest.mark.asyncio
async def test_an_unknown_host_key_is_rejected(ssh_server, archive_root, tmp_path):
    """Host key verification is the whole point of shipping a known_hosts file."""
    empty_known_hosts = tmp_path / "empty_known_hosts"
    empty_known_hosts.touch()

    store = SshArchiveStore(
        SshArchiveSettings(
            host=ssh_server.host,
            port=ssh_server.port,
            username=ssh_server.username,
            dir=PurePosixPath(archive_root),
            private_key_file=ssh_server.client_key_file,
            known_hosts_file=empty_known_hosts,
        )
    )

    with pytest.raises(asyncssh.HostKeyNotVerifiable):
        async with store.open():
            pass


def test_missing_private_key_is_rejected_on_construction(tmp_path):
    with pytest.raises(ArchiveError):
        SshArchiveStore(SshArchiveSettings(host="file01", private_key_file=tmp_path / "nonexistent"))


def test_missing_known_hosts_is_rejected_on_construction(ssh_server, tmp_path):
    with pytest.raises(ArchiveError):
        SshArchiveStore(
            SshArchiveSettings(
                host="file01",
                private_key_file=ssh_server.client_key_file,
                known_hosts_file=tmp_path / "nonexistent",
            )
        )


def test_host_key_verification_is_never_disabled(store):
    """asyncssh reads known_hosts=None as 'accept any host key', so we must never send it."""
    assert store.connect_options()["known_hosts"] is not None


def test_usable_ssh_settings_select_the_ssh_store(ssh_server, archive_root):
    store = create_archive_store(
        SshArchiveSettings(
            host=ssh_server.host,
            port=ssh_server.port,
            dir=PurePosixPath(archive_root),
            private_key_file=ssh_server.client_key_file,
            known_hosts_file=ssh_server.known_hosts_file,
        )
    )

    assert isinstance(store, SshArchiveStore)


@pytest.fixture
def nameless_local_account(monkeypatch):
    """A container running as a bare uid: nothing can name the local account.

    This is what the ingest image did before it created a real account, and
    what any deployment overriding runAsUser can still do.
    """
    for variable in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        monkeypatch.delenv(variable, raising=False)

    def no_such_uid(uid):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    monkeypatch.setattr(pwd, "getpwuid", no_such_uid)

    with pytest.raises(KeyError):
        getpass.getuser()


@pytest.mark.asyncio
async def test_uploads_when_the_local_account_has_no_name(
    nameless_local_account, ssh_server, archive_root, source_file
):
    """asyncssh wants a local username even though we authenticate as another."""
    store = SshArchiveStore(
        SshArchiveSettings(
            host=ssh_server.host,
            port=ssh_server.port,
            username=ssh_server.username,
            dir=PurePosixPath(archive_root),
            private_key_file=ssh_server.client_key_file,
            known_hosts_file=ssh_server.known_hosts_file,
        )
    )

    async with store.open() as archive:
        await archive.put(source_file, DESTINATION)

    assert (archive_root / DESTINATION).read_bytes() == b"video payload"
