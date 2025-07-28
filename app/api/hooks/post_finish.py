import logging
from pathlib import Path

from fastapi import HTTPException

from app.api.hooks.metadata import get_upload_metadata
from app.api.hooks.schema import HookRequest
from app.archive_store import Archive
from app.django_client.service import DjangoApiService
from app.ingest import Ingester
from app.media.converter import Converter
from app.media.ffprobe import do_probe
from runner import Runner

logger = logging.getLogger(__name__)


class ComplianceError(Exception):
    """Custom exception for compliance errors."""

    pass


def assert_compliance(metadata):
    try:
        assert metadata.format.nb_streams > 0, "File has no streams"
        assert metadata.format.nb_programs == 1, "File must have exactly one program"
        assert metadata.format.duration is not None, "File metadata does not contain duration"
        assert metadata.format.duration > 5, "File duration must be greater than 5 seconds"
        print(metadata.format.duration)
    except AssertionError as e:
        raise ComplianceError(e) from e


async def post_finish(hook_request: HookRequest, django_api: DjangoApiService):
    upload_meta = get_upload_metadata(hook_request)
    ingest = Ingester(django_api=django_api, converter=Converter(Runner()), archive=Archive())

    # As it's presently configured, tusd sees it at /upload/<video_id>/<sanitized_filename>
    # and it's mounted at ./upload for ingest.
    # It's a janky way to turn an absolute path to a relative one, but it works
    upload_file = Path(f".{hook_request.event.upload.storage['Path']}")

    if not upload_file.exists():
        logger.error(f"tusd handed off a file at {upload_file} but I couldn't find it there")
        raise HTTPException(status_code=500, detail="Internal error: Failed to hand over from upload to ingest")

    try:
        metadata = await do_probe(upload_file)
        logger.info("Got metadata, %d streams", len(metadata.streams))
    except Exception as e:
        logger.error("Failed to probe file: %s", e)
        raise HTTPException(status_code=400, detail="We could not make sense of this file, sorry") from e

    try:
        assert_compliance(metadata)
    except ComplianceError as e:
        logger.error("File failed compliance check: %s", e)
        raise HTTPException(status_code=400, detail=f"File failed compliance check: {e}") from e

    try:
        await ingest.ingest(upload_meta.video_id, upload_file, metadata)
    except Exception as e:
        logger.error("Failed to ingest file: %s", e)
        return {}
