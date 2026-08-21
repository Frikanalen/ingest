import logging
import shlex
from enum import StrEnum
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.api.hooks.schema.request import HookRequest
from app.media.ffprobe_schema import FfprobeOutput
from app.runner import Task

logger = logging.getLogger(__name__)


class UploadKind(StrEnum):
    VIDEO = "video"
    PROGRAM_IMAGE = "program_image"


class ImageRole(StrEnum):
    NETWORK_LOGO = "network_logo"
    CHANNEL_LOGO = "channel_logo"
    SHOW_LOGO = "show_logo"
    SHOW_STILL = "show_still"
    EPISODE_STILL = "episode_still"
    KEY_ART_TITLED = "key_art_titled"
    KEY_ART_UNTITLED = "key_art_untitled"
    BEHIND_THE_SCENES = "behind_the_scenes"
    LOCATION = "location"
    NEWS_EVENT = "news_event"
    PORTRAIT_HEADSHOT = "portrait_headshot"
    PORTRAIT_HALF_BODY = "portrait_half_body"
    PORTRAIT_FULL_BODY = "portrait_full_body"
    CAST_ENSEMBLE = "cast_ensemble"


class UploadMetaData(BaseModel):
    video_id: str = Field(..., alias="videoID")
    orig_file_name: str = Field(..., alias="origFileName")
    upload_token: str = Field(..., alias="uploadToken")
    upload_kind: UploadKind = Field(UploadKind.VIDEO, alias="uploadKind")
    image_role: ImageRole | None = Field(None, alias="imageRole")

    @model_validator(mode="after")
    def image_upload_has_a_role(self):
        if not self.video_id.isdigit():
            raise ValueError("videoID must be numeric")
        if self.upload_kind == UploadKind.PROGRAM_IMAGE and self.image_role is None:
            raise ValueError("imageRole is required for a programme image")
        if self.upload_kind == UploadKind.VIDEO and self.image_role is not None:
            raise ValueError("imageRole is only valid for a programme image")
        return self


def get_upload_metadata(hook: HookRequest) -> UploadMetaData:
    try:
        return UploadMetaData(**hook.event.upload.meta_data.model_dump())
    except ValidationError as e:
        errors = e.errors(include_context=False)
        logger.error("Metadata validation error: %s", errors)
        raise HTTPException(status_code=422, detail=errors) from e
    except AttributeError as e:
        logger.error("Metadata validation error: %s", e)
        raise HTTPException(status_code=422, detail="Missing required fields") from e


class MetadataExtractor:
    """Class to handle metadata extraction and compliance checking."""

    async def _run_ffprobe(self, filepath: Path) -> str:
        stdout, _ = await Task(
            f"ffprobe -v quiet -show_format -show_streams -of json {shlex.quote(str(filepath))}",
        ).execute()
        return stdout

    async def do_probe(self, filepath: Path) -> FfprobeOutput:
        logger.info("Probing file: %s", filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"File {filepath} does not exist")
        data = await self._run_ffprobe(filepath)
        logger.debug("Validating ffprobe output against JSON Schema: %s", data)
        return FfprobeOutput.model_validate_json(data)

    async def assert_compliance(self, upload_file: Path) -> FfprobeOutput:
        try:
            metadata = await self.do_probe(upload_file)
            assert metadata.format.nb_streams > 0, "File has no streams"
            assert metadata.format.duration is not None, "File metadata does not contain duration"
            assert float(metadata.format.duration) > 5, "File duration must be greater than 5 seconds"
        except AssertionError as e:
            raise ComplianceError(e) from e
        return metadata


class ComplianceError(Exception):
    """Custom exception for compliance errors."""

    pass
