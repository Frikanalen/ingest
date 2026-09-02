"""What `plan` and `apply` each decide to look at.

The two subcommands share their selection arguments but not their chores, and
that difference is the whole subject here. `apply` can only offer a video to
the queue, so the only chores it may consider are the ones a worker will run
when it claims one. `gc` is not among them and cannot be: an ingest job belongs
to a video, and a video the catalogue has deleted has none.

Getting that wrong is not a cosmetic error. A whole-catalogue `apply` used to
plan `gc` too, find a destructive action for every archived directory the
catalogue had dropped, and refuse the entire run -- queueing nothing, including
every legitimate format rebuild, and exiting 1 on what looked like a normal
day. The documented way to resume a backfill did nothing at all.
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
#: In the catalogue, source still under the name the old system gave it.
LEGACY_BROADCAST = "200"
#: In the archive, gone from the catalogue. Only `gc` has anything to say.
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
def legacy_video(archive_root, django_api) -> str:
    """A catalogue video whose source is still under the old `broadcast/` name.

    The one thing an `apply` can plan that does move media out of the published
    tree, which is what keeps `--yes` from being decoration.
    """
    place(archive_root, f"{LEGACY_BROADCAST}/broadcast/source.mp4")
    django_api.catalogue.videos.append(video_row(LEGACY_BROADCAST))
    django_api.catalogue.files.append(
        videofile(2, LEGACY_BROADCAST, VideoFileVariantEnum.BROADCAST, f"{LEGACY_BROADCAST}/broadcast/source.mp4")
    )
    return LEGACY_BROADCAST


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


def test_apply_refuses_gc(capsys):
    """Rejected at the parser, with somewhere to go rather than a list of letters."""
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["apply", "--chore", "gc"])

    assert exit_code.value.code == 2
    assert "fk-backfill gc" in capsys.readouterr().err


def test_apply_still_confirms_work_that_moves_media(run, django_api, legacy_video, capsys):
    """`--yes` is not decoration: the legacy migration trashes broadcast/.

    That chore is one a worker runs, so the confirmation is being asked for
    something that will actually happen -- unlike the `gc` findings it used to
    be asked for.
    """
    assert run(["apply", legacy_video]) == 1
    assert "move media out of the published tree" in capsys.readouterr().out
    django_api.enqueue_ingest_job.assert_not_awaited()

    assert run(["apply", "--yes", legacy_video]) == 0
    assert queued(django_api) == {legacy_video}


def test_plan_still_reports_the_orphan(run, django_api, capsys):
    """gc stays visible where it changes nothing, which is the point of `plan`."""
    assert run(["plan"]) == 0

    output = capsys.readouterr().out
    assert f"trash {ORPHAN}" in output
    django_api.enqueue_ingest_job.assert_not_awaited()


def test_apply_does_not_read_the_archive_root(run, django_api, monkeypatch):
    """Without gc there is nothing in an orphan directory worth a round trip.

    Read as a claim about cost, not tidiness: over SFTP this is one listing per
    directory the catalogue has dropped, before every chore declines to act.
    """
    seen = []
    original = cli.Observer.archived_video_ids

    async def record(self):
        seen.append(True)
        return await original(self)

    monkeypatch.setattr(cli.Observer, "archived_video_ids", record)

    run(["apply"])
    assert not seen

    run(["plan"])
    assert seen
