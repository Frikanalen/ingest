from pathlib import Path, PurePosixPath

#: Where a video's editorial stills live. Named because it is the one directory
#: under <id>/ that is not derived from the original: the images are registered
#: in a different table and describe the programme rather than its media, so an
#: upload that supersedes the media has no business taking them with it.
IMAGES_DIR = "images"


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


def program_image_location(video_id: str, image_id: str, extension: str) -> PurePosixPath:
    """Archive location for a validated editorial image."""

    assert video_id.isdigit(), "video_id must be a number"
    if not image_id.isalnum():
        raise ValueError("image_id must be alphanumeric")
    if extension not in {".jpg", ".png", ".webp"}:
        raise ValueError(f"unsupported programme-image extension: {extension}")
    return PurePosixPath(video_id) / IMAGES_DIR / f"{image_id}{extension}"
