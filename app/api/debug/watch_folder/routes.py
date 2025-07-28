from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.util.settings import settings

from .watcher import watch_directory

router = APIRouter()


@router.get("")
async def watch_directory_endpoint():
    return StreamingResponse(watch_directory(settings.debug.watchdir), media_type="text/event-stream")
