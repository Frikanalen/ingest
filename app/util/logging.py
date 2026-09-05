import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: The video the current task is working on, if any. A context variable rather
#: than filter state because the filter outlives any one video: the loggers it
#: is attached to are module-level and shared by every job the process runs.
_current_video_id: ContextVar[str | None] = ContextVar("current_video_id", default=None)


class VideoIdFilter(logging.Filter):
    """Logging filter that stamps the current video's id onto each record.

    Install one per logger, once -- `stamp_video_id` does that -- and scope the
    id itself with that context manager. Attaching a fresh filter per video
    instead would leave one behind on every job, growing the logger's filter
    chain for the life of the process and letting a finished job's id keep
    stamping records long after it ended.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        video_id = _current_video_id.get()
        if video_id is None:
            return True

        record.video_id = video_id
        if not hasattr(record, "extra"):
            record.extra = {}
        record.extra["video_id"] = video_id
        return True


@contextmanager
def stamp_video_id(logger: logging.Logger, video_id: str) -> Iterator[None]:
    """Tag everything `logger` emits in this scope with `video_id`.

    The filter is installed at most once per logger; the id is per-context, so
    concurrent tasks each stamp their own and none is left set afterwards.
    """
    if not any(isinstance(existing, VideoIdFilter) for existing in logger.filters):
        logger.addFilter(VideoIdFilter())

    token = _current_video_id.set(video_id)
    try:
        yield
    finally:
        _current_video_id.reset(token)
