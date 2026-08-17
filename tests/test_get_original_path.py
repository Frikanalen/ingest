from pathlib import Path, PurePosixPath

import pytest

from app.util.file_name_utils import derived_file_location, original_file_location


def test_valid_input():
    video_id = "12345"
    original_file_name = Path("example_video.mp4")
    expected_path = PurePosixPath("12345/original/example_video.mp4")
    assert expected_path == original_file_location(video_id, original_file_name)


def test_invalid_video_id_non_digit():
    video_id = "abcd"
    original_file_name = Path("example_video.mp4")
    with pytest.raises(AssertionError):
        original_file_location(video_id, original_file_name)


def test_original_file_name_with_path():
    video_id = "12345"
    original_file_name = Path("/some/path/example_video.mp4")
    with pytest.raises(ValueError):
        original_file_location(video_id, original_file_name)


def test_empty_video_file_name():
    video_id = "12345"
    original_file_name = Path("")
    with pytest.raises(ValueError):
        original_file_location(video_id, original_file_name)


def test_original_file_name_with_special_characters():
    video_id = "12345"
    original_file_name = Path("video_@_example!.mp4")
    expected_path = PurePosixPath("12345/original/video_@_example!.mp4")
    assert expected_path == original_file_location(video_id, original_file_name)


def test_derived_file_location_uses_format_as_directory():
    expected_path = PurePosixPath("12345/webm_med/example_video.webm")
    assert expected_path == derived_file_location("12345", "webm_med", Path("example_video.webm"))


def test_derived_file_location_discards_leading_path():
    """Generated files arrive from scratch space, so only the name may survive."""
    expected_path = PurePosixPath("12345/large_thumb/example_video.jpg")
    assert expected_path == derived_file_location("12345", "large_thumb", Path("/tmp/scratch/example_video.jpg"))


def test_derived_file_location_rejects_non_numeric_video_id():
    with pytest.raises(AssertionError):
        derived_file_location("abcd", "webm_med", Path("example_video.webm"))
