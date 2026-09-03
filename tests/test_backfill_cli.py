"""What `plan` and `apply` decide to look at.

The catalogue, and only the catalogue. An archived directory the catalogue has
dropped is not this tool's subject at all -- there is no job to queue for a
video that does not exist -- and reclaiming it is `fk-archive-gc`, on the host
holding the archive.

That is a stronger statement than it sounds. A whole-catalogue `apply` used to
plan `gc` too, find a destructive action for every archived directory the
catalogue had dropped, and refuse the entire run: queueing nothing, including
every legitimate format rebuild, and exiting 1 on what looked like a normal
day. The documented way to resume a backfill did nothing at all. These tests
are what keep an orphan directory from ever being in the way again.
"""

from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import IngestStateEnum, VideoFileVariantEnum

from app.archive_store import LocalArchiveStore
from app.backfill import cli

#: In the catalogue, source archived, no derivatives: needs every format.
NEEDS_FORMATS = "100"
#: In the archive, gone from the catalogue. Nothing here has anything to say.
ORPHAN = "300"


def video_row(video_id, duration="00:01:00", framerate=25, proper_import=True):
    return SimpleNamespace(id=int(video_id), duration=duration, framerate=framerate, proper_import=proper_import)


def page(rows):
    return SimpleNamespace(count=len(rows), results=list(rows))


def video_page(rows, limit, offset, proper_import):
    """Pages like django-api, and filters on `proper_import` like it too.

    Omitting the filter there returns only the videos whose ingest finished,
    so a mock that ignored it would let these tests pass over a catalogue
    production never sees the same way.
    """
    matching = [row for row in rows if row.proper_import is proper_import]
    return page(matching[offset : offset + limit])


def videofile(row_id, video_id, variant, filename):
    return SimpleNamespace(
        id=row_id,
        video=int(video_id),
        variant=variant,
        filename=filename,
        integrated_lufs=-23.0,
        profile_revision=0,
        additional_properties={},
    )


def place(root: Path, path: str) -> None:
    target = root / PurePosixPath(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"media")


@pytest.fixture
def archive_root(tmp_path) -> Path:
    """One video needing formats, and one directory the catalogue has dropped."""
    root = tmp_path / "archive"
    root.mkdir()
    place(root, f"{NEEDS_FORMATS}/original/source.mp4")
    place(root, f"{ORPHAN}/original/source.mp4")
    return root


@pytest.fixture
def django_api() -> AsyncMock:
    """A catalogue holding one of the two archived videos.

    Duration, framerate and loudness are filled in so the metadata chore stays
    quiet: what these tests are about is which videos get queued, and a chore
    firing on every one of them would make that indistinguishable.
    """
    videos = [video_row(NEEDS_FORMATS)]
    files = [videofile(1, NEEDS_FORMATS, VideoFileVariantEnum.ORIGINAL, f"{NEEDS_FORMATS}/original/source.mp4")]

    api = AsyncMock()
    api.list_videos_page.side_effect = lambda limit, offset, *, proper_import: video_page(
        videos, limit, offset, proper_import
    )
    api.list_video_files_page.side_effect = lambda limit, offset: page(files[offset : offset + limit])
    api.get_ingest_job.return_value = SimpleNamespace(state=IngestStateEnum.DONE)
    api.catalogue = SimpleNamespace(videos=videos, files=files)
    return api


@pytest.fixture
def run(monkeypatch, archive_root, django_api):
    """`fk-backfill` against that archive and that catalogue."""
    store = LocalArchiveStore(archive_root)

    async def with_services(handler):
        return await handler(None, store, django_api)

    monkeypatch.setattr(cli, "_with_services", with_services)
    return cli.main


def queued(django_api) -> set[str]:
    return {str(call.args[0]) for call in django_api.enqueue_ingest_job.await_args_list}


def test_apply_over_the_whole_catalogue_queues_the_work(run, django_api, capsys):
    """The bug this file exists for: an orphan directory used to veto the run.

    Nothing a worker can do about video 300 -- it has no catalogue row and so
    no job -- but the format work video 100 needs is real and must still go in.
    """
    assert run(["apply"]) == 0
    assert NEEDS_FORMATS in queued(django_api)
    assert "1 videos queued" in capsys.readouterr().out


def test_apply_leaves_the_orphan_alone(run, django_api):
    """Exactly the catalogue video, and nothing else.

    Enqueueing a deleted one would not merely be pointless: an ingest job is
    keyed on its video, so the write fails and lands in the run's failure
    count, which is then the thing a person reads the report for.
    """
    assert run(["apply"]) == 0

    assert queued(django_api) == {NEEDS_FORMATS}


def test_gc_says_where_it_went(capsys):
    """`fk-backfill gc` was documented, so it is in runbooks and shell
    histories. Argparse would answer it with "invalid choice"."""
    assert cli.main(["gc"]) == 2

    printed = capsys.readouterr().err
    assert "fk-archive-gc" in printed
    assert "storage host" in printed


def test_gc_says_where_it_went_however_it_was_invoked(capsys):
    assert cli.main(["gc", "--yes", "--max-delete-fraction", "0.5"]) == 2
    assert "fk-archive-gc" in capsys.readouterr().err


def test_neither_subcommand_reports_the_orphan(run, django_api, capsys):
    """It is not a finding here any more; it is a finding on the storage host."""
    assert run(["plan"]) == 0

    output = capsys.readouterr().out
    assert ORPHAN not in output
    django_api.enqueue_ingest_job.assert_not_awaited()


def test_neither_subcommand_reads_an_orphan_directory(run, monkeypatch):
    """Read as a claim about cost, not tidiness: over SFTP this is one listing
    per directory the catalogue has dropped, before every chore declines to
    act on it. There are thousands of them."""
    observed = []
    original = cli.Observer._archived_directories

    async def record(self, video_id):
        observed.append(video_id)
        return await original(self, video_id)

    monkeypatch.setattr(cli.Observer, "_archived_directories", record)

    run(["apply"])
    run(["plan"])

    assert observed == [NEEDS_FORMATS, NEEDS_FORMATS]
