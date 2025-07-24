from pathlib import Path

import pytest

from app.util.file_name_utils import original_file_location


def test_valid_input():
    video_id = "12345"
    original_file_name = Path("example_video.mp4")
    expected_path = Path("12345/original/example_video.mp4")
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
    expected_path = Path("12345/original/video_@_example!.mp4")
    assert expected_path == original_file_location(video_id, original_file_name)
