from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.util.settings import IngestAppSettings, get_settings

from .watcher import watch_directory

router = APIRouter()


@router.get("/tusFiles")
async def watch_downloads(settings: Annotated[IngestAppSettings, Depends(get_settings)]):
    return StreamingResponse(watch_directory(settings.tusd_dir), media_type="text/event-stream")


@router.get("/archive")
async def watch_archive(settings: Annotated[IngestAppSettings, Depends(get_settings)]):
    return StreamingResponse(watch_directory(settings.archive_dir), media_type="text/event-stream")
