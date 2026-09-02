from datetime import datetime
from http import HTTPStatus

from frikanalen_django_api_client import AuthenticatedClient
from frikanalen_django_api_client.api.ingest import ingest_claim
from frikanalen_django_api_client.api.videofiles import (
    videofiles_create,
    videofiles_destroy,
    videofiles_list,
    videofiles_partial_update,
)
from frikanalen_django_api_client.api.videos import (
    videos_images_create,
    videos_ingest_report,
    videos_ingest_retrieve,
    videos_list,
    videos_partial_update,
    videos_retrieve,
    videos_upload_token_verify,
)
from frikanalen_django_api_client.models import (
    IngestClaimRequest,
    IngestJobRequest,
    IngestKindEnum,
    IngestStateEnum,
    MediaTypeEnum,
    PatchedVideoFileRequest,
    PatchedVideoRequest,
    ProgramImageRegistrationRequest,
    RoleEnum,
    UploadTokenVerificationRequest,
    VideoFileRequest,
    VideoFileVariantEnum,
)
from frikanalen_django_api_client.types import UNSET, Response

from app.media.loudness.loudness_measurement import LoudnessMeasurement
from app.util.pprint_object_list import pprint_object_list


class DjangoApiError(Exception):
    """django-api answered with something other than the success we wanted.

    The generated client only raises by itself for statuses the schema does
    not document; a documented failure -- a rejected upload token included
    -- comes back as a parsed body instead. Callers here want the failure,
    so it becomes one, carrying the status for whoever has an HTTP request
    of their own to answer.
    """

    def __init__(self, status_code: int, content: bytes = b""):
        super().__init__(f"django-api returned {status_code}: {content.decode(errors='ignore')}")
        self.status_code = status_code


def _expect(response: Response, status: HTTPStatus) -> None:
    if response.status_code != status:
        raise DjangoApiError(response.status_code, response.content)


class DjangoApiService:
    client: AuthenticatedClient

    def __init__(self, client: AuthenticatedClient):
        self.client = client

    async def verify_upload_token(self, video_id: str, upload_token: str) -> None:
        response = await videos_upload_token_verify.asyncio_detailed(
            int(video_id),
            client=self.client,
            body=UploadTokenVerificationRequest(upload_token=upload_token),
        )
        _expect(response, HTTPStatus.NO_CONTENT)

    async def set_video_duration(self, video_id: str, duration: str):
        return await videos_partial_update.asyncio(
            int(video_id), client=self.client, body=PatchedVideoRequest(duration=duration)
        )

    async def set_video_uploaded_time(self, video_id: str, uploaded_time: datetime):
        return await videos_partial_update.asyncio(
            int(video_id), client=self.client, body=PatchedVideoRequest(uploaded_time=uploaded_time)
        )

    async def set_video_proper_import(self, video_id: str, proper_import: bool):
        return await videos_partial_update.asyncio(
            int(video_id), client=self.client, body=PatchedVideoRequest(proper_import=proper_import)
        )

    async def report_ingest_state(
        self,
        video_id: str,
        state: IngestStateEnum,
        percentage_done: int | None = None,
        status_text: str = "",
        error_code: str = "",
    ):
        """Replace what django-api knows about this video's ingest.

        The whole state goes every time, so a retried report says the same
        thing as the first one did.
        """
        return await videos_ingest_report.asyncio(
            int(video_id),
            client=self.client,
            body=IngestJobRequest(
                state=state,
                percentage_done=percentage_done,
                status_text=status_text,
                error_code=error_code,
            ),
        )

    async def get_ingest_job(self, video_id: str):
        """How far ingest has got with this video.

        A video nothing has ever reported on answers `pending` from an unsaved
        row: reading does not put anything in the queue.
        """
        return await videos_ingest_retrieve.asyncio(int(video_id), client=self.client)

    async def enqueue_ingest_job(self, video_id: str, kind: str, priority: int):
        """Put a video in the queue for a worker to pick up.

        `kind` has to be sent explicitly: a video that has never been reported
        on has no row yet, and the column it would default to says the source
        is a fresh upload -- which for a backfill is the one thing it is not,
        and would leave the job claimable only by the pod that cannot do it.
        """
        return await videos_ingest_report.asyncio(
            video_id,
            client=self.client,
            body=IngestJobRequest(
                state=IngestStateEnum.PENDING,
                kind=IngestKindEnum(kind),
                priority=priority,
            ),
        )

    async def claim_ingest_job(self, worker: str, kind: str | None = None):
        """Take the next job off the queue, or None if there is nothing to take.

        The whole decision happens in one statement on django-api's side --
        which job, and marking it taken -- so two workers asking at the same
        moment cannot come away with the same video. Nothing here needs to
        retry a lost race, because there is no race to lose.

        `kind` is what a worker can reach rather than what it prefers: an
        upload's source is in the tusd volume, which only one pod has. Omitting
        it means "anything", which is right for a pool that can reach both.
        """
        return await ingest_claim.asyncio(
            client=self.client,
            body=IngestClaimRequest(
                worker=worker,
                kind=IngestKindEnum(kind) if kind else UNSET,
            ),
        )

    async def get_video(self, video_id: str):
        return await videos_retrieve.asyncio(int(video_id), client=self.client)

    async def get_files_for_video(self, video_id: str):
        return await videofiles_list.asyncio(client=self.client, video_id=int(video_id))

    async def create_video_file(
        self,
        filename: str,
        video_id: str,
        file_format: VideoFileVariantEnum,
        loudness: LoudnessMeasurement | None = None,
        profile_revision: int | None = None,
    ):
        """Register a file against a video, with its loudness if we have it.

        The loudness columns live on the videofile rather than the video,
        so they describe the file that was actually measured: the figures
        playout levels from belong to the original, not to a derivative
        that has already been normalized to something else.

        `profile_revision` says which iteration of the template produced the
        file, which is what lets a reconciler find output made by a profile we
        have since moved past without inspecting the file itself.
        """
        req = VideoFileRequest(
            filename=str(filename),
            video=int(video_id),
            variant=file_format,
            integrated_lufs=loudness.integrated_lufs if loudness else UNSET,
            truepeak_lufs=loudness.truepeak_lufs if loudness else UNSET,
            # Left unset rather than sent as 0 for a file no template produced,
            # so the original keeps saying "no profile" instead of claiming to
            # predate one.
            profile_revision=profile_revision if profile_revision is not None else UNSET,
        )
        return await videofiles_create.asyncio(client=self.client, body=req)

    async def create_program_image(
        self,
        *,
        video_id: str,
        role: RoleEnum,
        filename: str,
        media_type: MediaTypeEnum,
        width: int,
        height: int,
    ) -> None:
        """Register an image only after the archive has published it."""

        response = await videos_images_create.asyncio_detailed(
            int(video_id),
            client=self.client,
            body=ProgramImageRegistrationRequest(
                role=role,
                filename=filename,
                media_type=media_type,
                width=width,
                height=height,
            ),
        )
        _expect(response, HTTPStatus.CREATED)

    async def retag_video_file(self, file_id: int, file_format: VideoFileVariantEnum, filename: str):
        """Point an existing videofile row at a new path and variant.

        Used when a legacy `broadcast/` directory turns out to be the original:
        the record is updated rather than replaced, so the file keeps whatever
        history and identity the row already carried.
        """
        return await videofiles_partial_update.asyncio(
            file_id,
            client=self.client,
            body=PatchedVideoFileRequest(
                variant=file_format,
                filename=str(filename),
            ),
        )

    async def set_video_file_loudness(self, file_id: int, loudness: LoudnessMeasurement):
        return await videofiles_partial_update.asyncio(
            file_id,
            client=self.client,
            body=PatchedVideoFileRequest(
                integrated_lufs=loudness.integrated_lufs,
                truepeak_lufs=loudness.truepeak_lufs,
            ),
        )

    async def delete_video_file(self, file_id: int):
        """Drop a videofile row whose file we are deliberately removing.

        Never called because a file turned out to be missing: that is an
        incident, and the row is the only remaining record that it existed.
        """
        return await videofiles_destroy.asyncio_detailed(file_id, client=self.client)

    async def set_video_framerate(self, video_id: str, framerate_milli: int):
        """Record the source's frame rate, in thousandths of a frame per second.

        Ingest works the exact rate out anyway -- DASH segments have to fall on
        whole frames, so it has to -- and until recently had nowhere to put it.
        """
        return await videos_partial_update.asyncio(
            video_id,
            client=self.client,
            body=PatchedVideoRequest(framerate=framerate_milli),
        )

    async def list_videos_page(self, limit: int, offset: int, *, proper_import: bool):
        """One page of the catalogue, ordered so paging is stable.

        Ordered by id rather than by upload time: a backfill pages through the
        whole catalogue while uploads are still arriving, and paging by
        anything that new rows sort into shifts rows between pages under you.

        `proper_import` has no default on purpose. django-api's own default,
        when the parameter is omitted, is true -- the endpoint then returns
        only videos whose ingest finished, which is the public catalogue and
        not the whole database. That is a sensible default for a public list
        and a dangerous one here: the caller that reads this is building the
        picture gc uses to decide which archived media no longer belongs to
        any video, and it reads absence as permission to trash. Omitting the
        filter made every in-flight and every failed ingest look deleted. So
        there is nothing to omit -- ask for a half, and if you want the whole
        database ask for both.
        """
        return await videos_list.asyncio(
            client=self.client,
            limit=limit,
            offset=offset,
            ordering="id",
            proper_import=proper_import,
        )

    async def list_video_files_page(self, limit: int, offset: int):
        return await videofiles_list.asyncio(client=self.client, limit=limit, offset=offset, ordering="id")

    async def get_videos(self, limit=10):
        return (await videos_list.asyncio(client=self.client, limit=limit, ordering="-uploaded_time")).results or []


if __name__ == "__main__":
    import asyncio

    async def main():
        from app.util.api_get_key import api_get_key
        from app.util.settings import get_settings

        settings = get_settings()
        token = api_get_key(
            str(settings.api.url),
            settings.api.username,
            settings.api.password.get_secret_value(),
        )

        service = DjangoApiService(
            AuthenticatedClient(
                base_url=str(get_settings().api.url),
                token=token,
                raise_on_unexpected_status=True,
                follow_redirects=True,
            )
        )
        videos = await service.get_videos()
        pprint_object_list(
            videos,
            [
                "id",
                "organization.name",
                "name",
            ],
        )

    asyncio.run(main())
