from datetime import datetime
from enum import Enum

from frikanalen_django_api_client import AuthenticatedClient
from frikanalen_django_api_client.api.videofiles import (
    videofiles_create,
    videofiles_destroy,
    videofiles_list,
    videofiles_partial_update,
)
from frikanalen_django_api_client.api.videos import videos_ingest_report, videos_list, videos_partial_update
from frikanalen_django_api_client.models import (
    IngestJobRequest,
    IngestStateEnum,
    PatchedVideoFileRequest,
    PatchedVideoRequest,
    VideoFileRequest,
    VideoFileVariantEnum,
)
from frikanalen_django_api_client.types import UNSET

from app.media.loudness.loudness_measurement import LoudnessMeasurement
from app.util.pprint_object_list import pprint_object_list


class FormatEnum(str, Enum):
    BROADCAST = "broadcast"
    CLOUDFLARE_ID = "cloudflare_id"
    DASH = "dash"
    LARGE_THUMB = "large_thumb"
    MED_THUMB = "med_thumb"
    ORIGINAL = "original"
    SMALL_THUMB = "small_thumb"
    SRT = "srt"
    THEORA = "theora"
    VC1 = "vc1"

    def __str__(self) -> str:
        return str(self.value)


class DjangoApiService:
    client: AuthenticatedClient

    def __init__(self, client: AuthenticatedClient):
        self.client = client

    async def verify_upload_token(self, video_id: str, upload_token: str) -> None:
        response = await self.client.get_async_httpx_client().post(
            f"/api/videos/{video_id}/upload_token/verify", json={"uploadToken": upload_token}
        )
        response.raise_for_status()

    async def set_video_duration(self, video_id: str, duration: str):
        return await videos_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest(duration=duration)
        )

    async def set_video_uploaded_time(self, video_id: str, uploaded_time: datetime):
        return await videos_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest(uploaded_time=uploaded_time)
        )

    async def set_video_proper_import(self, video_id: str, proper_import: bool):
        return await videos_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest(proper_import=proper_import)
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
            video_id,
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
        file_format: FormatEnum,
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
            variant=VideoFileVariantEnum[file_format.name],
            integrated_lufs=loudness.integrated_lufs if loudness else UNSET,
            truepeak_lufs=loudness.truepeak_lufs if loudness else UNSET,
        )

        if profile_revision is not None:
            # Sent as an extra property until schema.yaml catches up with
            # django-api's profileRevision column and the client is
            # regenerated, at which point this becomes an ordinary argument
            # above. An API that does not know the field yet drops it, and the
            # row reads back as 0 -- "produced before we tracked this", and so
            # as stale. Shipping this ahead of the migration therefore costs a
            # redundant re-encode later, never a row that lies about itself.
            req["profileRevision"] = profile_revision

        return await videofiles_create.asyncio(client=self.client, body=req)

    async def create_program_image(
        self,
        *,
        video_id: str,
        role: str,
        filename: str,
        media_type: str,
        width: int,
        height: int,
    ) -> None:
        """Register an image only after the archive has published it."""

        response = await self.client.get_async_httpx_client().post(
            f"/api/videos/{int(video_id)}/images",
            json={
                "role": role,
                "filename": filename,
                "mediaType": media_type,
                "width": width,
                "height": height,
            },
        )
        response.raise_for_status()

    async def retag_video_file(self, file_id: int, file_format: FormatEnum, filename: str):
        """Point an existing videofile row at a new path and variant.

        Used when a legacy `broadcast/` directory turns out to be the original:
        the record is updated rather than replaced, so the file keeps whatever
        history and identity the row already carried.
        """
        return await videofiles_partial_update.asyncio(
            file_id,
            client=self.client,
            body=PatchedVideoFileRequest(
                variant=VideoFileVariantEnum[file_format.name],
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

        Ingest has always computed this -- DASH segments have to fall on whole
        frames -- and has never been able to store it, because the field is
        read-only until django-api's change lands. Sent the same way as
        profileRevision until then; dropped by an API that does not know it
        yet, which leaves the field empty and the next run trying again.
        """
        body = PatchedVideoRequest()
        body["framerate"] = framerate_milli
        return await videos_partial_update.asyncio(video_id, client=self.client, body=body)

    async def list_videos_page(self, limit: int, offset: int):
        """One page of the catalogue, ordered so paging is stable.

        Ordered by id rather than by upload time: a backfill pages through the
        whole catalogue while uploads are still arriving, and paging by
        anything that new rows sort into shifts rows between pages under you.
        """
        return await videos_list.asyncio(client=self.client, limit=limit, offset=offset, ordering="id")

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
