from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.get_settings import get_settings
from app.util.settings import Settings

from .watcher import watch_directory

router = APIRouter()


@router.get("/tusFiles")
async def watch_downloads(settings: Annotated[Settings, Depends(get_settings)]):
    return StreamingResponse(watch_directory(settings.debug.watchdir), media_type="text/event-stream")


@router.get("/archive")
async def watch_archive(settings: Annotated[Settings, Depends(get_settings)]):
    return StreamingResponse(watch_directory(settings.archive_dir), media_type="text/event-stream")
