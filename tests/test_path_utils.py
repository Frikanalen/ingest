from pathlib import Path

import pytest

from app.util.exceptions import SkippableError
from app.util.path_utils import get_single_file_from_directory


def test_single_file_in_directory(tmp_path):
    test_file = tmp_path / "test_file.txt"
    test_file.touch()

    result = get_single_file_from_directory(tmp_path)
    assert result == test_file


def test_no_file_in_directory(tmp_path):
    with pytest.raises(SkippableError):
        get_single_file_from_directory(tmp_path)


def test_multiple_files_in_directory(tmp_path):
    (tmp_path / "file1.txt").touch()
    (tmp_path / "file2.txt").touch()

    with pytest.raises(SkippableError):
        get_single_file_from_directory(tmp_path)


def test_non_existent_directory():
    test_dir = Path("non_existent_dir")
    with pytest.raises(FileNotFoundError):
        get_single_file_from_directory(test_dir)
