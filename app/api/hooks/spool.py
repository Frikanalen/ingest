"""Clearing out image uploads nothing is coming back for.

A video upload is spooled at `<video-id>/<filename>`, so the next attempt lands
on the same path and pre-create can simply delete what is in the way. An image
upload cannot work like that: a video may legitimately have several images, so
each gets its own `<video-id>/image_uploads/<upload-id>/` directory, and no
later upload ever names the same path again.

That leaves nothing to clear the failures. An image whose registration failed
is deliberately kept -- a retry finishes the work, which is what makes the
post-finish hook idempotent -- but if the retry never comes, the file stays in
the upload volume for good. That volume is ReadWriteOnce and is the reason this
pod runs as a single replica; it is not a place for things to accumulate.

So the failures are collected by age rather than by being overwritten, at the
moment another image for the same video is spooled. Not a background task: the
only thing that puts files here is an image upload, so the only time the pile
can have grown is when another one arrives.
"""

import shutil
import time
from logging import getLogger
from pathlib import Path

logger = getLogger(__name__)

IMAGE_UPLOAD_DIR = "image_uploads"

#: How long an image upload directory may sit untouched before it is taken to
#: be abandoned. Generously longer than any retry: the cost of waiting is one
#: file of at most 10 MB, and the cost of being hasty is deleting an upload
#: that is still arriving.
ABANDONED_AFTER_S = 24 * 60 * 60


def clear_abandoned_image_uploads(
    tusd_dir: Path,
    video_id: str,
    *,
    abandoned_after_s: float = ABANDONED_AFTER_S,
) -> int:
    """Remove this video's image uploads that nothing has touched in a day.

    Scoped to one video because that is what the caller knows it is about, and
    because walking every video directory in the volume to find a few megabytes
    would cost more than it reclaims.

    Errors are logged and swallowed. This is housekeeping on the way to
    accepting an upload, and failing the upload because an old file could not
    be removed would trade a leak for a refusal.
    """
    spool = tusd_dir / video_id / IMAGE_UPLOAD_DIR
    cutoff = time.time() - abandoned_after_s
    removed = 0

    try:
        candidates = sorted(spool.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return 0
    except OSError as error:
        logger.warning("Could not read the image upload spool at %s: %s", spool, error)
        return 0

    for upload in candidates:
        try:
            if not upload.is_dir() or _last_touched(upload) > cutoff:
                continue
            shutil.rmtree(upload)
        except OSError as error:
            logger.warning("Could not clear the abandoned image upload %s: %s", upload, error)
            continue

        logger.info("Cleared abandoned image upload %s", upload)
        removed += 1

    return removed


def _last_touched(upload: Path) -> float:
    """The newest mtime in an upload directory.

    The directory's own mtime only moves when an entry is added or removed, and
    tusd writes into files that already exist -- so an upload still streaming in
    would look untouched since it started if this asked the directory alone.
    """
    return max((entry.stat().st_mtime for entry in upload.iterdir()), default=upload.stat().st_mtime)
