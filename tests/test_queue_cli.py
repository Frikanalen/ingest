"""What the queue-side tools decide to look at, and to queue.

Two things are being pinned here. Each tool runs its own chore and only its
own, so `scan-metadata.py` cannot quietly queue a catalogue-wide re-encode and
`backfill.py` cannot decide a video's duration is wrong. And both consider the
catalogue and only the catalogue: a video django-api does not have cannot be
queued, because an ingest job is keyed on its video and the PUT would fail.

Nothing here reads an archive, and there is no archive in these tests to read.
That is the property that lets an operator run these with an API token and no
SSH key -- the plan's actions come out of the videofile rows, and the worker
that claims the video looks at the archive before doing anything.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import IngestStateEnum, VideoFileVariantEnum

from app.formats import DESIRED_FORMATS, current_revision
from fk_queue import cli

#: Original registered, no derivatives, metadata complete.
NEEDS_FORMATS = "100"
#: Every current format registered, but nothing ever recorded its frame rate.
NEEDS_METADATA = "200"
#: Converged. Neither tool has anything to say about it.
SETTLED = "300"
#: The legacy shape: a source, but under the name the previous system used.
#: Nothing can be derived from it until the migration on the storage host has
#: run, so it is reported and never queued.
LEGACY = "400"
#: Not in the catalogue at all.
MISSING = "999"


def video_row(video_id, duration="00:01:00", framerate=25000, proper_import=True):
    return SimpleNamespace(id=int(video_id), duration=duration, framerate=framerate, proper_import=proper_import)


def videofile(row_id, video_id, variant, revision=0, lufs=-23.0):
    return SimpleNamespace(
        id=row_id,
        video=int(video_id),
        variant=variant,
        filename=f"{video_id}/{variant}/file",
        integrated_lufs=lufs,
        profile_revision=revision,
        additional_properties={},
    )


def current_formats(video_id, first_row_id):
    """Every desired format, registered at the revision shipped right now."""
    return [
        videofile(first_row_id + n, video_id, file_format, revision=current_revision(file_format))
        for n, file_format in enumerate(DESIRED_FORMATS)
    ]


def pager(rows):
    async def fetch(limit: int, offset: int, *, proper_import=None):
        matching = rows if proper_import is None else [row for row in rows if row.proper_import is proper_import]
        return SimpleNamespace(count=len(matching), results=matching[offset : offset + limit])

    return fetch


@pytest.fixture
def django_api() -> AsyncMock:
    videos = [
        video_row(NEEDS_FORMATS),
        video_row(NEEDS_METADATA, framerate=None),
        video_row(SETTLED),
        video_row(LEGACY),
    ]
    files = [
        videofile(1, NEEDS_FORMATS, VideoFileVariantEnum.ORIGINAL),
        videofile(2, NEEDS_METADATA, VideoFileVariantEnum.ORIGINAL),
        *current_formats(NEEDS_METADATA, 10),
        videofile(3, SETTLED, VideoFileVariantEnum.ORIGINAL),
        *current_formats(SETTLED, 20),
        videofile(4, LEGACY, VideoFileVariantEnum.BROADCAST),
    ]

    api = AsyncMock()
    api.list_videos_page = pager(videos)
    api.list_video_files_page = pager(files)
    api.get_ingest_job.return_value = SimpleNamespace(state=IngestStateEnum.DONE)
    return api


@pytest.fixture
def run(monkeypatch, django_api):
    """Either tool, against that catalogue and no archive."""

    class NoClient:
        """The generated client, minus ever issuing a request."""

        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(
        cli.credentials,
        "load",
        lambda **_: cli.credentials.Credentials(api_url="http://api.invalid", token="secret", environment="testing"),
    )
    monkeypatch.setattr(cli, "AuthenticatedClient", NoClient)
    monkeypatch.setattr(cli, "DjangoApiService", lambda _: django_api)

    def go(chore: str, argv: list[str]) -> int:
        return cli.main(argv, prog="test", description="test", chore=chore)

    return go


def queued(django_api) -> set[str]:
    return {str(call.args[0]) for call in django_api.enqueue_ingest_job.await_args_list}


def test_without_apply_nothing_is_queued(run, django_api, capsys):
    assert run("formats", []) == 0

    django_api.enqueue_ingest_job.assert_not_awaited()
    assert "Nothing was changed" in capsys.readouterr().out


def test_apply_queues_the_videos_the_chore_found_work_in(run, django_api, capsys):
    assert run("formats", ["--apply"]) == 0

    assert queued(django_api) == {NEEDS_FORMATS}
    assert "1 videos queued" in capsys.readouterr().out


def test_each_tool_runs_only_its_own_chore(run, django_api):
    """The video short of a frame rate has every current format, and the one
    short of formats has its metadata. Neither tool may reach the other's."""
    assert run("metadata", ["--apply"]) == 0

    assert queued(django_api) == {NEEDS_METADATA}


def test_a_converged_video_is_never_queued(run, django_api):
    assert run("formats", ["--apply"]) == 0
    assert run("metadata", ["--apply"]) == 0

    assert SETTLED not in queued(django_api)


def test_a_video_the_catalogue_does_not_have_is_reported_and_left_alone(run, django_api, caplog):
    """Enqueueing it would not merely be pointless: an ingest job is keyed on
    its video, so the write fails and lands in the run's failure count, which
    is then the thing a person reads the report for."""
    assert run("formats", ["--apply", NEEDS_FORMATS, MISSING]) == 0

    assert queued(django_api) == {NEEDS_FORMATS}
    assert MISSING in caplog.text


def test_limit_stops_after_that_many_videos(run, django_api):
    assert run("formats", ["--apply", "--limit", "1"]) == 0

    assert queued(django_api) == {NEEDS_FORMATS}


def test_quiet_prints_the_summary_without_the_videos(run, capsys):
    assert run("formats", ["-q"]) == 0

    output = capsys.readouterr().out
    assert f"video {NEEDS_FORMATS}" not in output
    assert "4 videos looked at, 1 need something done." in output


def test_a_video_that_is_only_worth_reporting_is_reported_and_not_queued(run, django_api, capsys):
    """Its source is still called `broadcast`, which is the storage host's
    one-shot migration to settle and nothing a worker can be handed. The count
    is how an operator sees how much of that is left, so it has to survive a
    summary otherwise made of things there is work in."""
    assert run("formats", ["--apply"]) == 0

    assert LEGACY not in queued(django_api)
    assert "1  no original is registered" in capsys.readouterr().out


def test_a_tool_can_only_name_a_chore_a_worker_runs(run):
    """It can only offer a video to the queue, so a chore nothing would run
    means a plan printed and nothing ever carried out."""
    with pytest.raises(ValueError, match="not a chore"):
        run("reticulate-splines", [])
