import pytest

from fk_archive_utils.archive_path import parse_file_path, parse_removable_path, parse_variant_path
from fk_archive_utils.errors import UsageError


@pytest.mark.parametrize(
    "raw",
    [
        "1/original/video.mov",
        "0/images/abc123.jpg",
        "123456/dash/manifest.mpd",
        "12/original/Programmet æøå.mov",
        "12/original/a file with spaces.mov",
    ],
)
def test_accepts_the_shapes_the_archive_actually_holds(raw):
    assert str(parse_file_path(raw)) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "/12/original/a.mov",
        "12/../13/original/a.mov",
        "12/original/../../etc/passwd",
        "../12/original/a.mov",
        "12/original",
        "12/original/nested/a.mov",
        "12/.trash/a.mov",
        "12/.spool/a.mov",
        "12//a.mov",
        "12/original/.hidden",
        "twelve/original/a.mov",
        "012/original/a.mov",
        "12/original/a\\b.mov",
        "12/original/a\nb.mov",
        "12/original/ leading.mov",
        "",
    ],
)
def test_refuses_anything_else(raw):
    with pytest.raises(UsageError):
        parse_file_path(raw)


def test_a_component_longer_than_the_filesystem_allows_is_refused():
    with pytest.raises(UsageError, match="longer than"):
        parse_file_path(f"12/original/{'a' * 256}")


def test_removable_paths_are_a_video_or_one_directory_in_one():
    assert parse_removable_path("12").parts == ("12",)
    assert parse_removable_path("12/dash").parts == ("12", "dash")


@pytest.mark.parametrize("raw", ["12/dash/manifest.mpd", ".trash", "12/../13", ""])
def test_removable_paths_refuse_everything_else(raw):
    with pytest.raises(UsageError):
        parse_removable_path(raw)


def test_variant_path_is_built_from_exactly_two_checked_arguments():
    assert parse_variant_path("12", "dash_preview").parts == ("12", "dash_preview")


@pytest.mark.parametrize("video_id", ["../etc", "007", "", "twelve"])
def test_variant_path_refuses_malformed_video_ids(video_id):
    with pytest.raises(UsageError):
        parse_variant_path(video_id, "dash_preview")


@pytest.mark.parametrize("variant", ["a/b", "..", ".hidden", ".trash", "dash_preview "])
def test_variant_path_refuses_malformed_variants(variant):
    with pytest.raises(UsageError):
        parse_variant_path("12", variant)
