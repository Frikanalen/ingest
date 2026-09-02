"""Reclaiming media the catalogue no longer has a video for.

The sweep is small; the guards are the point. It is the one operation whose
blast radius is the whole archive, and the failure it most has to survive is
not a bug in any of the code it calls -- it is being pointed at one
environment's catalogue and another's archive, where every individual decision
is locally correct and only the total is insane.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.archive_store import LocalArchiveStore
from app.archive_store.base import TRASH_DIR
from app.backfill.observe import IncompleteSnapshot
from app.backfill.sweep import Sweep, TooMuchGarbage


def video_row(video_id, proper_import=True):
    return SimpleNamespace(id=video_id, duration="00:10:00", framerate=25000, proper_import=proper_import)


def pager(rows, count=None):
    """Pages like django-api, and filters on `proper_import` like it too.

    The filter is the point: omitting it there returns only videos whose
    ingest finished, so a mock that ignored it would let the sweep look
    correct while production trashed every upload still in flight.
    """

    async def fetch(limit: int, offset: int, *, proper_import=None):
        matching = rows if proper_import is None else [row for row in rows if row.proper_import is proper_import]
        total = len(matching) if count is None else count
        return SimpleNamespace(count=total, results=matching[offset : offset + limit])

    return fetch


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


def place(archive_root, path, payload=b"media"):
    target = archive_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


#: Catalogue-sized on purpose. The ceiling is a proportion, so a fixture of
#: ten videos makes a single orphan 9% of the archive and every test that
#: expects a sweep to proceed fails on a guard that is working correctly.
CATALOGUE_SIZE = 100


@pytest.fixture
def catalogue(archive_root):
    """An archive and a catalogue that agree completely."""
    for i in range(1, CATALOGUE_SIZE + 1):
        place(archive_root, f"{i}/original/source.mp4")

    api = AsyncMock()
    api.list_videos_page = pager([video_row(i) for i in range(1, CATALOGUE_SIZE + 1)])
    api.list_video_files_page = pager([])
    return api


@pytest.fixture
def sweep(archive_root, catalogue, tmp_path):
    return Sweep(LocalArchiveStore(archive_root), catalogue, work_dir=tmp_path)


@pytest.mark.asyncio
async def test_an_archive_matching_the_catalogue_has_nothing_to_reclaim(sweep):
    report = await sweep.run(apply=True)

    assert report.orphans == []
    assert report.archived == CATALOGUE_SIZE


@pytest.mark.asyncio
async def test_media_for_a_deleted_video_is_found_and_measured(sweep, archive_root, catalogue):
    place(archive_root, "8842/original/orphan.mp4", b"x" * 4096)

    report = await sweep.run(apply=False)

    assert report.orphans == ["8842"]
    assert report.reclaimable_bytes == 4096


@pytest.mark.asyncio
async def test_a_dry_run_moves_nothing(sweep, archive_root):
    place(archive_root, "8842/original/orphan.mp4")

    await sweep.run(apply=False)

    assert (archive_root / "8842" / "original" / "orphan.mp4").exists()
    assert not (archive_root / TRASH_DIR).exists()


@pytest.mark.asyncio
async def test_applying_moves_the_orphan_into_the_trash(sweep, archive_root):
    place(archive_root, "8842/original/orphan.mp4")

    report = await sweep.run(apply=True)

    assert report.trashed == ["8842"]
    assert not (archive_root / "8842").exists()
    assert list((archive_root / TRASH_DIR).rglob("orphan.mp4"))


@pytest.mark.asyncio
async def test_videos_the_catalogue_still_has_are_untouched(sweep, archive_root):
    place(archive_root, "8842/original/orphan.mp4")

    await sweep.run(apply=True)

    assert (archive_root / "1" / "original" / "source.mp4").exists()


@pytest.mark.asyncio
async def test_it_refuses_when_too_much_of_the_archive_is_unaccounted_for(sweep, archive_root):
    """The shape of pointing FK_API_URL at staging and the archive at
    production: every decision locally correct, the total catastrophic."""
    for i in range(1000, 1100):
        place(archive_root, f"{i}/original/orphan.mp4")

    with pytest.raises(TooMuchGarbage, match="same environment"):
        await sweep.run(apply=True)


@pytest.mark.asyncio
async def test_refusing_happens_before_anything_moves(sweep, archive_root):
    for i in range(1000, 1100):
        place(archive_root, f"{i}/original/orphan.mp4")

    with pytest.raises(TooMuchGarbage):
        await sweep.run(apply=True)

    assert not (archive_root / TRASH_DIR).exists()
    assert (archive_root / "1000" / "original" / "orphan.mp4").exists()


@pytest.mark.asyncio
async def test_a_dry_run_reports_what_it_would_refuse_to_do(sweep, archive_root):
    """Seeing the number is how you find out which environment you are in."""
    for i in range(1000, 1100):
        place(archive_root, f"{i}/original/orphan.mp4")

    report = await sweep.run(apply=False)

    assert len(report.orphans) == 100
    assert report.share > 0.02


@pytest.mark.asyncio
async def test_the_ceiling_can_be_raised_deliberately(archive_root, catalogue, tmp_path):
    place(archive_root, "8842/original/orphan.mp4")
    for i in range(1000, 1050):
        place(archive_root, f"{i}/original/orphan.mp4")

    sweep = Sweep(LocalArchiveStore(archive_root), catalogue, max_delete_fraction=1.0, work_dir=tmp_path)
    report = await sweep.run(apply=True)

    assert len(report.trashed) == 51


@pytest.mark.asyncio
async def test_a_short_catalogue_read_stops_the_sweep(sweep, catalogue, archive_root):
    """A partial catalogue makes the archive look garbage in exactly the
    proportion the read fell short by."""
    place(archive_root, "8842/original/orphan.mp4")
    catalogue.list_videos_page = pager([video_row(1)], count=CATALOGUE_SIZE)

    with pytest.raises(IncompleteSnapshot):
        await sweep.run(apply=True)

    assert (archive_root / "8842").exists()


@pytest.mark.asyncio
async def test_the_archives_own_directories_are_never_swept(sweep, archive_root):
    place(archive_root, ".trash/20260101T000000Z/1/original/old.mp4")
    place(archive_root, ".spool/2/original/partial.mp4")

    report = await sweep.run(apply=True)

    assert report.orphans == []
    assert (archive_root / ".spool" / "2" / "original" / "partial.mp4").exists()


@pytest.mark.asyncio
async def test_a_video_still_being_ingested_is_not_an_orphan(sweep, archive_root, catalogue):
    """The archive holds the original before ingest finishes, and django-api's
    video list omits unfinished videos unless asked for them. Read only that
    list and a video part-way through ingest is indistinguishable from media
    for a video somebody deleted -- so the sweep trashes the original out from
    under the transcode that is still running."""
    place(archive_root, "8842/original/source.mp4")
    catalogue.list_videos_page = pager(
        [video_row(i) for i in range(1, CATALOGUE_SIZE + 1)] + [video_row(8842, proper_import=False)]
    )

    report = await sweep.run(apply=True)

    assert report.orphans == []
    assert (archive_root / "8842" / "original" / "source.mp4").exists()


@pytest.mark.asyncio
async def test_a_video_whose_ingest_failed_is_not_an_orphan(sweep, archive_root, catalogue):
    """`proper_import` stays false for good once an ingest fails, so this is
    not a narrow window -- such a video is gc-eligible forever."""
    place(archive_root, "8842/original/source.mp4")
    catalogue.list_videos_page = pager(
        [video_row(i) for i in range(1, CATALOGUE_SIZE + 1)] + [video_row(8842, proper_import=False)]
    )

    await sweep.run(apply=True)

    assert not (archive_root / TRASH_DIR).exists()


@pytest.mark.asyncio
async def test_a_video_the_catalogue_really_has_dropped_is_still_reclaimed(sweep, archive_root):
    """The other half of the same change: widening what counts as "exists"
    must not stop gc doing the job it is for."""
    place(archive_root, "8842/original/orphan.mp4")

    report = await sweep.run(apply=True)

    assert report.trashed == ["8842"]
