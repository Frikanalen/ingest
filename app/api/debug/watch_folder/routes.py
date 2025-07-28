from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.util.settings import settings

from .watcher import watch_directory

router = APIRouter()


@router.get("/tusFiles")
async def watch_downloads():
    return StreamingResponse(watch_directory(settings.debug.watchdir), media_type="text/event-stream")


@router.get("/archive")
async def watch_archive():
    return StreamingResponse(watch_directory(settings.archive_dir), media_type="text/event-stream")
