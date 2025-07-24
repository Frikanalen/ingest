import logging


class VideoIdFilter(logging.Filter):
    """logging filter to add video_id to log record."""

    def __init__(self, video_id):
        super().__init__()
        self.video_id = video_id

    def filter(self, record):
        record.video_id = self.video_id
        if not hasattr(record, "extra"):
            record.extra = {}
        record.extra["video_id"] = self.video_id
        return True
