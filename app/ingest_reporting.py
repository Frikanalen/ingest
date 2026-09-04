from collections.abc import Awaitable, Callable
from enum import StrEnum
from logging import getLogger

from frikanalen_django_api_client.models import IngestStateEnum

from app.django_client.service import DjangoApiService

logger = getLogger(__name__)


class IngestErrorCode(StrEnum):
    """Why an ingest stopped, in terms the frontend can act on.

    These are the vocabulary shared with django-api's `errorCode` and, in
    turn, with whatever the uploader is eventually told. They name the
    situation rather than the exception, because the wording shown to a
    person is the frontend's to choose -- an uploader needs to know they
    should send a different file, not which coroutine raised.
    """

    #: The file is not something we can broadcast: wrong codec, wrong
    #: container, no video stream.
    NOT_COMPLIANT = "not_compliant"
    #: ffprobe could not make sense of the file at all.
    UNREADABLE = "unreadable"
    #: The original could not be stored. Nothing to do with the upload.
    ARCHIVE_FAILED = "archive_failed"
    #: A derived file could not be produced from a compliant original.
    TRANSCODE_FAILED = "transcode_failed"
    #: Anything else. The detail is in status_text and the logs.
    INTERNAL_ERROR = "internal_error"


class IngestReporter:
    """Publishes where a video's ingest has got to, and never raises.

    Every failure to report is logged and dropped, deliberately. A status
    report is worth strictly less than the ingest it describes: losing one
    leaves the uploader looking at a stale state for a while, whereas
    letting it abort the run would lose the video itself.
    """

    def __init__(self, django_api: DjangoApiService, video_id: str):
        self.django_api = django_api
        self.video_id = video_id
        self.failure_reported = False

    async def state(self, state: IngestStateEnum, percentage_done: int | None = None) -> None:
        """Say where the pipeline is.

        `percentage_done` stays None for the states with nothing to count:
        an unmoving 0% reads as a stuck upload, where no number at all
        reads as work in progress.
        """
        await self._report(state, percentage_done=percentage_done)

    async def failed(self, error_code: IngestErrorCode, detail: str) -> None:
        """Record why the pipeline stopped.

        `detail` is operator-facing and django-api serves it to nobody:
        ffmpeg's complaints name archive paths, which are not an
        organization's business.
        """
        self.failure_reported = True
        await self._report(
            IngestStateEnum.FAILED,
            error_code=str(error_code),
            # The column holds 1000 characters, and ffmpeg's last words are
            # the informative ones when it says more than that.
            status_text=detail[-1000:],
        )

    async def failed_unless_already(self, error_code: IngestErrorCode, detail: str) -> None:
        """The backstop for whatever the pipeline did not classify itself.

        Every step that knows what its own failure means reports a code
        that says so; this exists to make sure the remainder still leaves
        the uploader with an answer, rather than a state that never moves.
        """
        if self.failure_reported:
            return
        await self.failed(error_code, detail)

    async def _report(self, state: IngestStateEnum, **fields) -> None:
        try:
            await self.django_api.report_ingest_state(self.video_id, state, **fields)
        except Exception:
            logger.warning(
                "Could not report ingest state %s for video %s",
                state,
                self.video_id,
                exc_info=True,
            )


def transcode_progress_reporter(reporter: IngestReporter) -> Callable[[float], Awaitable[None]]:
    """Turns ffmpeg's own -progress stream into a percentage.

    This is the DASH ladder's own completion fraction, and nothing else moves
    the bar -- which matches reality, since a 60s 1080p source measures the
    ladder at roughly 100x the cost of the three thumbnails combined.

    Nothing else moves it because nothing else is *given* it. The thumbnails
    could not: they are over before ffmpeg would have anything to report. The
    preview very much could -- it is a second encoding template, emitting the
    same -progress stream -- so `ProduceFormat.drives_progress` withholds this
    callback from it. Handed to both, the bar would run to 100 on the preview
    and start again from nothing on the ladder, which to a member watching is
    an import that restarted. The preview is minutes against the ladder's
    hours, so the honest thing for it to report is nothing at all.

    Reports are throttled to one per whole percentage point: ffmpeg's -progress
    stream updates far more often than that, and each report is a call to
    django-api. It is also what keeps a claim alive, since the lease is read
    from the same updated_time these reports move.
    """
    last_reported = -1

    async def report(fraction: float) -> None:
        nonlocal last_reported
        percentage = round(100 * fraction)
        if percentage != last_reported:
            last_reported = percentage
            await reporter.state(IngestStateEnum.TRANSCODING, percentage_done=percentage)

    return report
