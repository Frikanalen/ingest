import io
import json
from pathlib import Path

import pytest

from fk_archive_utils import cli
from fk_archive_utils.errors import UsageError


def invoke(profile_dir: Path, variant="dash_preview", video_id="12"):
    out = io.StringIO()
    code = cli.run(
        ["test", "delete-variant", variant, video_id],
        stdin=io.BytesIO(),
        stdout=out,
        profile_dir=profile_dir,
    )
    return code, json.loads(out.getvalue()) if out.getvalue() else None


def test_removes_preview_and_preserves_video_and_siblings(profile_dir: Path, archive_root: Path, make_file):
    make_file("12/dash_preview/manifest.mpd", b"manifest")
    make_file("12/dash_preview/chunks/one.m4s", b"one")
    make_file("12/original/a.mov", b"original")
    make_file("12/dash/manifest.mpd", b"full")
    make_file("12/images/cover.jpg", b"image")

    code, result = invoke(profile_dir)

    assert code == 0
    assert result == {
        "operation": "delete-variant",
        "path": "12/dash_preview",
        "deleted": True,
        "files_removed": 2,
        "bytes_removed": len(b"manifestone"),
    }
    assert not (archive_root / "12/dash_preview").exists()
    assert (archive_root / "12/original/a.mov").read_bytes() == b"original"
    assert (archive_root / "12/dash/manifest.mpd").read_bytes() == b"full"
    assert (archive_root / "12/images/cover.jpg").read_bytes() == b"image"


def test_absent_variant_is_success_and_creates_nothing(profile_dir: Path, archive_root: Path):
    code, result = invoke(profile_dir)

    assert code == 0
    assert result == {"operation": "delete-variant", "path": "12/dash_preview", "deleted": False}
    assert list(archive_root.iterdir()) == []


def test_deleting_twice_is_idempotent(profile_dir: Path, archive_root: Path, make_file):
    make_file("12/dash_preview/manifest.mpd")

    first_code, first = invoke(profile_dir)
    second_code, second = invoke(profile_dir)

    assert (first_code, first["deleted"]) == (0, True)
    assert (second_code, second["deleted"]) == (0, False)
    assert (archive_root / "12").is_dir()


@pytest.mark.parametrize("variant", ["original", "dash", "images", "srt"])
def test_non_deletable_variants_are_refused(profile_dir: Path, archive_root: Path, make_file, variant):
    content = make_file(f"12/{variant}/keep", variant.encode())

    code, result = invoke(profile_dir, variant=variant)

    assert (code, result) == (UsageError.exit_code, None)
    assert content.read_bytes() == variant.encode()
    assert not (archive_root / "12/dash_preview").exists()


@pytest.mark.parametrize("video_id", ["../etc", "007", "", "not-numeric"])
def test_malformed_video_ids_are_refused(profile_dir: Path, video_id):
    code, result = invoke(profile_dir, video_id=video_id)

    assert (code, result) == (UsageError.exit_code, None)


@pytest.mark.parametrize("variant", ["a/b", "..", ".hidden", ".trash", "trailing "])
def test_malformed_variants_are_refused(profile_dir: Path, variant):
    code, result = invoke(profile_dir, variant=variant)

    assert (code, result) == (UsageError.exit_code, None)


def test_symlink_target_is_refused_and_left_untouched(profile_dir: Path, archive_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    survivor = outside / "survivor"
    survivor.write_bytes(b"keep")
    (archive_root / "12").mkdir()
    (archive_root / "12/dash_preview").symlink_to(outside, target_is_directory=True)

    code, result = invoke(profile_dir)

    assert (code, result) == (UsageError.exit_code, None)
    assert survivor.read_bytes() == b"keep"
    assert (archive_root / "12/dash_preview").is_symlink()


def test_symlink_component_is_refused(profile_dir: Path, archive_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    preview = outside / "dash_preview"
    preview.mkdir(parents=True)
    survivor = preview / "survivor"
    survivor.write_bytes(b"keep")
    (archive_root / "12").symlink_to(outside, target_is_directory=True)

    code, result = invoke(profile_dir)

    assert (code, result) == (UsageError.exit_code, None)
    assert survivor.read_bytes() == b"keep"


def test_regular_file_at_variant_path_is_refused(profile_dir: Path, archive_root: Path, make_file):
    target = make_file("12/dash_preview", b"keep")

    code, result = invoke(profile_dir)

    assert (code, result) == (UsageError.exit_code, None)
    assert target.read_bytes() == b"keep"
