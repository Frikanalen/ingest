from pathlib import Path, PurePosixPath


def _check_video_id(video_id: str) -> None:
    """Refuse a video_id that must not become part of an archive path.

    Raised rather than asserted: `python -O` deletes an `assert`, and this is
    the last thing between a video_id and a constructed path. Only the upload
    path's video_id has been through `UploadMetaData`; a worker's comes from
    `str(job.video)` off the queue, so here this is the only check there is.
    """
    if not video_id.isdigit():
        raise ValueError("video_id must be a number")


def original_file_location(video_id: str, original_file_name: Path) -> PurePosixPath:
    """Returns the archive-relative path to the original file for video_id"""
    _check_video_id(video_id)
    if not original_file_name.name:
        raise ValueError("video_file must not be empty")
    if str(original_file_name) != original_file_name.name:
        raise ValueError(
            f"video_file must be a filename, not a path: {original_file_name} != {original_file_name.name}"
        )

    return PurePosixPath(video_id) / "original" / original_file_name.name


def derived_file_location(video_id: str, file_format: str, derived_file_name: Path) -> PurePosixPath:
    """Returns the archive-relative path to a generated file for video_id"""
    _check_video_id(video_id)

    return PurePosixPath(video_id) / file_format / derived_file_name.name


def program_image_location(video_id: str, image_id: str, extension: str) -> PurePosixPath:
    """Archive location for a validated editorial image."""

    _check_video_id(video_id)
    if not image_id.isalnum():
        raise ValueError("image_id must be alphanumeric")
    if extension not in {".jpg", ".png", ".webp"}:
        raise ValueError(f"unsupported programme-image extension: {extension}")
    return PurePosixPath(video_id) / "images" / f"{image_id}{extension}"
