import logging

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.api.hooks.schema import HookRequest

logger = logging.getLogger(__name__)


class UploadMetaData(BaseModel):
    video_id: str = Field(..., alias="videoID")
    orig_file_name: str = Field(..., alias="origFileName")
    upload_token: str = Field(..., alias="uploadToken")


def get_upload_metadata(hook: HookRequest) -> UploadMetaData:
    try:
        return UploadMetaData(**hook.event.upload.meta_data.model_dump())
    except ValidationError as e:
        logger.error("Metadata validation error: %s", e.errors())
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except AttributeError as e:
        logger.error("Metadata validation error", e)
        raise HTTPException(status_code=422, detail="Missing required fields") from e
