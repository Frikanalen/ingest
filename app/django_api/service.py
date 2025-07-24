from datetime import datetime
from typing import List

from fastapi import Depends
from frikanalen_django_api_client import AuthenticatedClient
from frikanalen_django_api_client.api.videofiles import videofiles_create, videofiles_list, videofiles_partial_update
from frikanalen_django_api_client.api.videos import videos_list, videos_partial_update
from frikanalen_django_api_client.models import PatchedVideoRequest, VideoFile, VideoFileRequest

from app.loudness.loudness_measurement import LoudnessMeasurement
from app.tus_hook.hook_server import build_client, get_client_from_app_state
from app.util.pprint_object_list import pprint_object_list


class DjangoApiService:
    client: AuthenticatedClient

    def __init__(self, state: AuthenticatedClient = Depends(get_client_from_app_state)):
        self.client = state

    async def set_video_duration(self, video_id: str, duration: str):
        return videos_partial_update.asyncio(video_id, client=self.client, body=PatchedVideoRequest(duration=duration))

    async def set_video_uploaded_time(self, video_id: str, uploaded_time: datetime):
        return videos_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest(uploaded_time=uploaded_time)
        )

    async def set_video_proper_import(self, video_id: str, proper_import: bool):
        return videos_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest(proper_import=proper_import)
        )

    async def get_original_files_without_loudness(self, limit=5) -> List[VideoFile]:
        return (
            await videofiles_list.asyncio(
                client=self.client,
                format_fsname="original",
                integrated_lufs_isnull=True,
                limit=limit,
                ordering="-video",
            )
        ).results or []

    async def set_video_loudness(self, video_id: str, loudness: LoudnessMeasurement):
        return videofiles_partial_update.asyncio(
            video_id, client=self.client, body=PatchedVideoRequest.from_dict(loudness)
        )

    async def get_files_for_video(self, video_id: str):
        return videofiles_list.asyncio(client=self.client, video_id=int(video_id))

    async def create_video_file(self, video_file: VideoFileRequest):
        return videofiles_create.asyncio(client=self.client, body=video_file)

    async def get_videos(self, limit=10):
        return (await videos_list.asyncio(client=self.client, limit=limit, ordering="-uploaded_time")).results or []


if __name__ == "__main__":
    import asyncio

    from app.tus_hook.hook_server import get_client_from_app_state

    async def main():
        service = DjangoApiService(build_client())
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
