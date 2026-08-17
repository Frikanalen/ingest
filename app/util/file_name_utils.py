from pathlib import Path, PurePosixPath


def original_file_location(video_id: str, original_file_name: Path) -> PurePosixPath:
    """Returns the archive-relative path to the original file for video_id"""
    assert video_id.isdigit(), "video_id must be a number"
    if str(original_file_name) != original_file_name.name:
        raise ValueError(
            f"video_file must be a filename, not a path: {original_file_name} != {original_file_name.name}"
        )

    assert original_file_name, "video_file must not be empty"

    return PurePosixPath(video_id) / "original" / original_file_name.name


def derived_file_location(video_id: str, file_format: str, derived_file_name: Path) -> PurePosixPath:
    """Returns the archive-relative path to a generated file for video_id"""
    assert video_id.isdigit(), "video_id must be a number"

    return PurePosixPath(video_id) / file_format / derived_file_name.name
