"""Reclaiming media for videos the catalogue no longer has.

The two guards are the subject: a catalogue read that came up short must never
be acted on, and the total share about to be trashed is checked once before
anything moves. Everything else is a comparison of two lists.
"""

import io
import re
from pathlib import Path

import pytest

from fk_archive_utils import gc
from fk_archive_utils.errors import IncompleteCatalogue
from fk_archive_utils.gc import TooMuchGarbage, archived_video_ids, human_bytes, sweep


class FakeCatalogue:
    """Stands in for django-api."""

    def __init__(self, known: set[str], *, incomplete: bool = False, api_url: str = "http://catalogue.invalid"):
        self.known = known
        self.incomplete = incomplete
        self.credentials = type("Credentials", (), {"api_url": api_url})()

    def video_ids(self) -> set[str]:
        if self.incomplete:
            raise IncompleteCatalogue("finished videos reported 900 rows but returned 12")
        return self.known


def archive_videos(make_file, *specs: tuple[str, int]) -> None:
    for video_id, size in specs:
        make_file(f"{video_id}/original/source.mp4", b"x" * size)


def test_a_video_the_catalogue_has_dropped_is_trashed(profile, archive_root: Path, make_file):
    archive_videos(make_file, ("12", 10), ("13", 10))

    # One of two archived videos is half the archive, so the share guard would
    # otherwise refuse this outright -- which is the guard working. It is
    # tested on an archive big enough for a share to mean something, below.
    report = sweep(profile, FakeCatalogue({"12"}), apply=True, max_delete_fraction=1.0)

    assert [orphan.video_id for orphan in report.orphans] == ["13"]
    assert not (archive_root / "13").exists()
    assert (archive_root / "12/original/source.mp4").exists()
    assert (archive_root / report.orphans[0].trashed_to / "original/source.mp4").exists()


def test_nothing_moves_without_apply(profile, archive_root: Path, make_file):
    archive_videos(make_file, ("13", 10))

    report = sweep(profile, FakeCatalogue(set()), apply=False)

    assert [orphan.video_id for orphan in report.orphans] == ["13"]
    assert report.orphans[0].trashed_to is None
    assert (archive_root / "13/original/source.mp4").exists()


def test_the_report_says_what_is_at_stake(profile, make_file):
    make_file("13/original/source.mp4", b"x" * 700)
    make_file("13/dash/manifest.mpd", b"y" * 300)
    make_file("13/images/cover.jpg", b"z" * 100)

    report = sweep(profile, FakeCatalogue(set()), apply=False)

    assert report.reclaimable_bytes == 1100
    assert report.share == 1.0


def test_the_archive_s_own_bookkeeping_is_not_a_video(profile, archive_root: Path, make_file):
    archive_videos(make_file, ("12", 10))
    (archive_root / ".trash/20200101T000000Z").mkdir(parents=True)
    (archive_root / ".spool").mkdir()
    (archive_root / "not-a-video").mkdir()

    assert archived_video_ids(profile) == ["12"]
    assert sweep(profile, FakeCatalogue({"12"}), apply=False).orphans == []


def test_an_archive_that_disagrees_wildly_is_refused(profile, archive_root: Path, make_file):
    """The failure this is for is the archive and the catalogue being different
    environments: every individual decision is locally correct, and only the
    total is insane."""
    archive_videos(make_file, *((str(video_id), 10) for video_id in range(100, 200)))

    with pytest.raises(TooMuchGarbage, match=re.escape("above the 2.0%")):
        sweep(profile, FakeCatalogue({"100"}), apply=True)

    assert (archive_root / "101/original/source.mp4").exists()


def test_the_refusal_names_both_ends_so_a_mismatch_is_visible(profile, make_file):
    archive_videos(make_file, *((str(video_id), 10) for video_id in range(100, 200)))

    with pytest.raises(TooMuchGarbage, match=re.escape("staging.example")):
        sweep(profile, FakeCatalogue(set(), api_url="https://staging.example"), apply=True)


def test_a_share_within_the_limit_proceeds(profile, archive_root: Path, make_file):
    archive_videos(make_file, *((str(video_id), 10) for video_id in range(100, 200)))
    known = {str(video_id) for video_id in range(100, 199)}

    report = sweep(profile, FakeCatalogue(known), apply=True, max_delete_fraction=0.02)

    assert [orphan.video_id for orphan in report.orphans] == ["199"]
    assert not (archive_root / "199").exists()


def test_the_limit_is_only_checked_when_something_would_move(profile, make_file):
    """Reporting on a wild disagreement is exactly what an operator needs in
    order to find out that it is one."""
    archive_videos(make_file, *((str(video_id), 10) for video_id in range(100, 200)))

    report = sweep(profile, FakeCatalogue(set()), apply=False)

    assert len(report.orphans) == 100


def test_a_catalogue_that_could_not_be_read_in_full_trashes_nothing(profile, archive_root: Path, make_file):
    """Absence is read as permission, so a partial answer must never be
    mistaken for a complete one."""
    archive_videos(make_file, ("13", 10))

    with pytest.raises(IncompleteCatalogue):
        sweep(profile, FakeCatalogue(set(), incomplete=True), apply=True)

    assert (archive_root / "13/original/source.mp4").exists()


def test_an_empty_archive_is_not_a_division_by_zero(profile):
    report = sweep(profile, FakeCatalogue(set()), apply=True)

    assert (report.share, report.orphans) == (0.0, [])


def test_the_run_reports_what_it_found(profile_dir: Path, make_file, monkeypatch):
    archive_videos(make_file, ("13", 1500))
    monkeypatch.setattr(gc, "load_credentials", _fake_credentials)
    monkeypatch.setattr(gc, "Catalogue", lambda credentials: FakeCatalogue(set()))
    out = io.StringIO()

    assert gc.run(["test"], stdout=out, profile_dir=profile_dir) == 0

    printed = out.getvalue()
    assert "13 (1.5 kB)" in printed
    assert "Nothing was changed" in printed


def test_the_run_reports_a_refusal_as_its_own_exit_code(profile_dir: Path, make_file, monkeypatch):
    archive_videos(make_file, *((str(video_id), 10) for video_id in range(100, 200)))
    monkeypatch.setattr(gc, "load_credentials", _fake_credentials)
    monkeypatch.setattr(gc, "Catalogue", lambda credentials: FakeCatalogue(set()))

    assert gc.run(["test", "--apply"], stdout=io.StringIO(), profile_dir=profile_dir) == TooMuchGarbage.exit_code


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 B"), (999, "999 B"), (1500, "1.5 kB"), (2_500_000_000, "2.5 GB")],
)
def test_sizes_are_reported_in_units_a_person_reads(count, expected):
    assert human_bytes(count) == expected


def _fake_credentials(environment, *, config_path=None, api_url=None):
    from fk_archive_utils.catalogue import Credentials

    return Credentials(api_url="http://catalogue.invalid", token="unused", environment=environment)
