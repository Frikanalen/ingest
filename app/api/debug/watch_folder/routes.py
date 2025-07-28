from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .watcher import watch_directory

router = APIRouter()


@router.get("/watch-directory")
async def watch_directory_endpoint():
    return StreamingResponse(watch_directory(), media_type="text/event-stream")
