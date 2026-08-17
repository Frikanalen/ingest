from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.util.settings import IngestAppSettings, LocalArchiveSettings, get_settings

from .watcher import watch_directory

router = APIRouter()


@router.get("/tusFiles")
async def watch_downloads(settings: Annotated[IngestAppSettings, Depends(get_settings)]):
    return StreamingResponse(watch_directory(settings.tusd_dir), media_type="text/event-stream")


@router.get("/archive")
async def watch_archive(settings: Annotated[IngestAppSettings, Depends(get_settings)]):
    if not isinstance(settings.archive, LocalArchiveSettings):
        raise HTTPException(status_code=409, detail="The archive is on another host and cannot be watched")

    return StreamingResponse(watch_directory(settings.archive.dir), media_type="text/event-stream")
