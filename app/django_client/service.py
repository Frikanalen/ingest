from datetime import datetime
from enum import Enum

from frikanalen_django_api_client import AuthenticatedClient
from frikanalen_django_api_client.api.videofiles import videofiles_create, videofiles_list, videofiles_partial_update
from frikanalen_django_api_client.api.videos import videos_list, videos_partial_update
from frikanalen_django_api_client.models import (
    PatchedVideoRequest,
    VideoFile,
    VideoFileRequest,
    VideofilesListFormatFsname,
)

from app.media.loudness.loudness_measurement import LoudnessMeasurement
from app.util.pprint_object_list import pprint_object_list


class FormatEnum(str, Enum):
    BROADCAST = "broadcast"
    CLOUDFLARE_ID = "cloudflare_id"
    LARGE_THUMB = "large_thumb"
    MED_THUMB = "med_thumb"
    ORIGINAL = "original"
    SMALL_THUMB = "small_thumb"
    SRT = "srt"
    THEORA = "theora"
    VC1 = "vc1"
    WEBM_MED = "webm_med"

    def __str__(self) -> str:
        return str(self.value)


class IntFormatEnum(int, Enum):
    LARGE_THUMB = 1
    BROADCAST = 2
    VC1 = 3
    MED_THUMB = 4
    SMALL_THUMB = 5
    ORIGINAL = 6
    THEORA = 7
    SRT = 8
    CLOUDFLARE_ID = 9
    WEBM_MED = 10


class DjangoApiService:
    client: AuthenticatedClient

    def __init__(self, client: AuthenticatedClient):
        self.client = client

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

    async def get_original_files_without_loudness(self, limit=5) -> list[VideoFile]:
        return (
            await videofiles_list.asyncio(
                client=self.client,
                format_fsname=VideofilesListFormatFsname.ORIGINAL,
                integrated_lufs_isnull=True,
                limit=limit,
                ordering="-video",
            )
        ).results or []

    async def set_video_loudness(self, video_id: str, loudness: LoudnessMeasurement):
        return await videofiles_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest.from_dict(loudness)
        )

    async def get_files_for_video(self, video_id: str):
        return await videofiles_list.asyncio(client=self.client, video_id=int(video_id))

    async def create_video_file(self, filename: str, video_id: str, file_format: FormatEnum):
        req = VideoFileRequest(
            filename=str(filename), video=int(video_id), format_=IntFormatEnum[file_format.name].value
        )
        return await videofiles_create.asyncio(client=self.client, body=req)

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
