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
