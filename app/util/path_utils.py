from pathlib import Path

from app.fk_exceptions import SkippableError


def get_single_file_from_directory(from_dir: Path) -> Path:
    """
    Get a single file name from the source directory.
    Raises SkippableError if no file is found.
    Returns the file name as string.
    """
    try:
        # asserts only one file in the directory.
        [file_name] = [x for x in from_dir.iterdir()]
        return file_name
    except ValueError:
        raise SkippableError("Found no file in %s" % from_dir)
