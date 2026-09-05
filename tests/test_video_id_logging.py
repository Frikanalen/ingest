"""Tagging log records with the video being worked on.

The worker process is long-lived and claims video after video, so anything
per-video that attaches itself to a module-level logger has to come off again:
left in place it grows without bound, and it keeps stamping a finished job's id
onto records belonging to the next one.
"""

import logging

from app.util.logging import VideoIdFilter, stamp_video_id


def test_records_inside_the_scope_carry_the_video_id(caplog):
    logger = logging.getLogger("test_video_id_logging.tagged")

    with caplog.at_level(logging.INFO, logger=logger.name), stamp_video_id(logger, "12345"):
        logger.info("working")

    (record,) = caplog.records
    assert record.video_id == "12345"
    assert record.extra["video_id"] == "12345"


def test_the_id_does_not_outlive_its_scope(caplog):
    logger = logging.getLogger("test_video_id_logging.after")

    with caplog.at_level(logging.INFO, logger=logger.name):
        with stamp_video_id(logger, "12345"):
            logger.info("claimed")
        logger.info("idle")

    claimed, idle = caplog.records
    assert claimed.video_id == "12345"
    assert not hasattr(idle, "video_id")


def test_a_second_video_replaces_the_first_rather_than_stacking(caplog):
    logger = logging.getLogger("test_video_id_logging.consecutive")

    with caplog.at_level(logging.INFO, logger=logger.name):
        for video_id in ("12345", "67890", "11111"):
            with stamp_video_id(logger, video_id):
                logger.info("working")

    assert [record.video_id for record in caplog.records] == ["12345", "67890", "11111"]
    assert len([f for f in logger.filters if isinstance(f, VideoIdFilter)]) == 1
