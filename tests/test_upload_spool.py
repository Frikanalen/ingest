"""Clearing image uploads nothing came back for.

Each image upload gets a path of its own, so unlike a video upload none of them
is ever overwritten by the next attempt. These are the cases that decide which
of them are abandoned and which are still someone's upload in progress.
"""

import os
import time

from app.api.hooks.spool import clear_abandoned_image_uploads

VIDEO_ID = "1234"
DAY = 24 * 60 * 60


def spool(tusd_dir, upload_id: str, *, age_s: float = 0.0, name: str = "key-art.png"):
    """One image upload directory, as tusd would have left it."""
    upload = tusd_dir / VIDEO_ID / "image_uploads" / upload_id
    upload.mkdir(parents=True)
    (upload / name).write_bytes(b"image bytes")
    (upload / f"{name}.info").write_text("{}")

    when = time.time() - age_s
    for path in (*upload.iterdir(), upload):
        os.utime(path, (when, when))
    return upload


def test_an_upload_nothing_came_back_for_is_cleared(tmp_path):
    abandoned = spool(tmp_path, "imageold", age_s=2 * DAY)

    assert clear_abandoned_image_uploads(tmp_path, VIDEO_ID) == 1
    assert not abandoned.exists()


def test_a_recent_upload_is_left_alone(tmp_path):
    """A member picking three images at once has three of these open at a time."""
    sibling = spool(tmp_path, "imagenew")

    assert clear_abandoned_image_uploads(tmp_path, VIDEO_ID) == 0
    assert sibling.exists()


def test_an_upload_still_arriving_is_judged_by_its_file(tmp_path):
    """tusd writes into a file that already exists, so the directory's own
    mtime stops moving the moment the upload starts."""
    upload = spool(tmp_path, "imageslow", age_s=2 * DAY)
    (upload / "key-art.png").write_bytes(b"another chunk just now")

    assert clear_abandoned_image_uploads(tmp_path, VIDEO_ID) == 0
    assert upload.exists()


def test_other_videos_are_not_touched(tmp_path):
    """Scoped to the video being uploaded for; nothing here walks the volume."""
    spool(tmp_path, "imageold", age_s=2 * DAY)
    other = tmp_path / "9999" / "image_uploads" / "imagealsoold"
    other.mkdir(parents=True)
    (other / "key-art.png").write_bytes(b"image bytes")
    os.utime(other, (time.time() - 2 * DAY, time.time() - 2 * DAY))

    clear_abandoned_image_uploads(tmp_path, VIDEO_ID)

    assert other.exists()


def test_the_video_upload_itself_is_never_swept(tmp_path):
    """A video spools at <id>/<filename>, beside the image_uploads directory."""
    spool(tmp_path, "imageold", age_s=2 * DAY)
    original = tmp_path / VIDEO_ID / "source.mp4"
    original.write_bytes(b"video bytes")
    os.utime(original, (time.time() - 30 * DAY, time.time() - 30 * DAY))

    clear_abandoned_image_uploads(tmp_path, VIDEO_ID)

    assert original.exists()


def test_a_video_that_has_never_had_an_image_is_not_an_error(tmp_path):
    assert clear_abandoned_image_uploads(tmp_path, VIDEO_ID) == 0


def test_one_unreadable_upload_does_not_stop_the_others(tmp_path):
    """Housekeeping on the way to accepting an upload: it must not fail it."""
    stray = tmp_path / VIDEO_ID / "image_uploads" / "imagestray"
    stray.parent.mkdir(parents=True)
    stray.write_text("not a directory")
    abandoned = spool(tmp_path, "imageold", age_s=2 * DAY)

    assert clear_abandoned_image_uploads(tmp_path, VIDEO_ID) == 1
    assert not abandoned.exists()
