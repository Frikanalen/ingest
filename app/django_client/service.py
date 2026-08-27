from datetime import datetime
from http import HTTPStatus

from frikanalen_django_api_client import AuthenticatedClient
from frikanalen_django_api_client.api.videofiles import videofiles_create, videofiles_list
from frikanalen_django_api_client.api.videos import (
    videos_images_create,
    videos_ingest_report,
    videos_list,
    videos_partial_update,
    videos_upload_token_verify,
)
from frikanalen_django_api_client.models import (
    IngestJobRequest,
    IngestStateEnum,
    MediaTypeEnum,
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

    async def get_files_for_video(self, video_id: str):
        return await videofiles_list.asyncio(client=self.client, video_id=int(video_id))

    async def create_video_file(
        self,
        filename: str,
        video_id: str,
        file_format: VideoFileVariantEnum,
        loudness: LoudnessMeasurement | None = None,
    ):
        """Register a file against a video, with its loudness if we have it.

        The loudness columns live on the videofile rather than the video,
        so they describe the file that was actually measured: the figures
        playout levels from belong to the original, not to a derivative
        that has already been normalized to something else.
        """
        req = VideoFileRequest(
            filename=str(filename),
            video=int(video_id),
            variant=file_format,
            integrated_lufs=loudness.integrated_lufs if loudness else UNSET,
            truepeak_lufs=loudness.truepeak_lufs if loudness else UNSET,
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
