from datetime import datetime

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
from app.util.settings import settings


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

    async def create_video_file(self, video_file: VideoFileRequest):
        return await videofiles_create.asyncio(client=self.client, body=video_file)

    async def get_videos(self, limit=10):
        return (await videos_list.asyncio(client=self.client, limit=limit, ordering="-uploaded_time")).results or []


if __name__ == "__main__":
    import asyncio

    async def main():
        service = DjangoApiService(
            AuthenticatedClient(
                base_url=str(settings.api.url),
                token=settings.api.key,
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
