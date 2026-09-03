from pathlib import Path

import pytest

from fk_archive_utils.errors import ProfileError
from fk_archive_utils.profile import DEFAULT_FILE_MODE, load


def write(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.toml").write_text(body)
    return directory


def test_reads_the_root_and_the_account_to_run_as(tmp_path: Path):
    directory = write(tmp_path, "prod", 'root = "/archive/media"\nmanager = "archive-manager"\n')

    loaded = load("prod", profile_dir=directory)

    assert loaded.root == Path("/archive/media")
    assert loaded.manager == "archive-manager"
    assert loaded.file_mode == DEFAULT_FILE_MODE


def test_modes_are_octal_strings(tmp_path: Path):
    body = 'root = "/archive/media"\nmanager = "m"\nfile_mode = "0640"\ndir_mode = "2775"\n'
    directory = write(tmp_path, "prod", body)

    loaded = load("prod", profile_dir=directory)

    assert loaded.file_mode == 0o640
    assert loaded.dir_mode == 0o2775


def test_a_mode_written_as_a_number_is_refused_rather_than_misread(tmp_path: Path):
    directory = write(tmp_path, "prod", 'root = "/a"\nmanager = "m"\nfile_mode = 644\n')

    with pytest.raises(ProfileError, match="octal string"):
        load("prod", profile_dir=directory)


def test_an_unknown_setting_is_refused_rather_than_ignored(tmp_path: Path):
    directory = write(tmp_path, "prod", 'root = "/a"\nmanager = "m"\nfile_modes = "0644"\n')

    with pytest.raises(ProfileError, match="unknown settings"):
        load("prod", profile_dir=directory)


@pytest.mark.parametrize(
    "body",
    [
        'manager = "m"\n',
        'root = "relative/path"\nmanager = "m"\n',
        'root = "/a"\n',
        'root = "/a"\nmanager = ""\n',
        'root = "/a"\nmanager = "m"\nsftp_server = "sftp-server"\n',
    ],
)
def test_an_unusable_profile_says_what_is_wrong_with_it(tmp_path: Path, body: str):
    directory = write(tmp_path, "prod", body)

    with pytest.raises(ProfileError):
        load("prod", profile_dir=directory)


def test_a_missing_profile_names_the_directory_it_looked_in(tmp_path: Path):
    with pytest.raises(ProfileError, match=str(tmp_path)):
        load("prod", profile_dir=tmp_path)


@pytest.mark.parametrize("name", ["../prod", "prod/../../etc/shadow", "PROD", "", "prod.toml"])
def test_a_profile_name_cannot_be_a_path(tmp_path: Path, name: str):
    with pytest.raises(ProfileError, match="not a profile name"):
        load(name, profile_dir=tmp_path)
