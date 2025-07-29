import logging
import shlex
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.api.hooks.schema import HookRequest
from app.media.ffprobe_schema import FfprobeOutput
from app.task_builder import TKB

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
        logger.error("Metadata validation error: %s", e)
        raise HTTPException(status_code=422, detail="Missing required fields") from e


class MetadataExtractor:
    """Class to handle metadata extraction and compliance checking."""

    async def _run_ffprobe(self, filepath: Path) -> str:
        stdout, _ = await TKB(
            f"ffprobe -v quiet -show_format -show_streams -of json {shlex.quote(str(filepath))}"
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
