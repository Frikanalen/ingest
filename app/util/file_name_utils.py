from pathlib import Path


def original_file_location(video_id: str, original_file_name: Path) -> Path:
    """Returns a path to the original file for video_id"""
    assert video_id.isdigit(), "video_id must be a number"
    if str(original_file_name) != original_file_name.name:
        raise ValueError(
            f"video_file must be a filename, not a path: {original_file_name} != {original_file_name.name}"
        )

    assert original_file_name, "video_file must not be empty"

    return Path(video_id) / "original" / original_file_name
