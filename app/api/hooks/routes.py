import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from starlette.exceptions import HTTPException

from app.api.hooks.metadata import ComplianceError, MetadataExtractor, get_upload_metadata
from app.api.hooks.pre_create import pre_create
from app.api.hooks.schema.request import HookRequest
from app.django_client.service import DjangoApiService
from app.get_settings import get_settings
from app.ingest import Ingester
from app.util.app_state import get_django_api, get_metadata_extractor
from app.util.settings import Settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/")
async def receive_hook(
    hook_request: HookRequest,
    settings: Settings = Depends(get_settings),
    django_api: DjangoApiService = Depends(get_django_api),
    metadata_extractor: MetadataExtractor = Depends(get_metadata_extractor),
):
    logger.info("Received hook: %s", hook_request.type)
    if hook_request.type == "pre-create":
        return await pre_create(hook_request)

    if hook_request.type == "post-finish":
        ingest = Ingester(archive_base_path=settings.archive_dir, django_api=django_api)
        upload_meta = get_upload_metadata(hook_request)

        # As it's presently configured, tusd sees it at /upload/<video_id>/<sanitized_filename>
        # and it's mounted at ./upload for ingest.
        # It's a janky way to turn an absolute path to a relative one, but it works
        upload_file = Path(f".{hook_request.event.upload.storage['Path']}")
        try:
            metadata = metadata_extractor.assert_compliance(upload_file)
        except ComplianceError as e:
            logger.error("File failed compliance check: %s", e)
            raise HTTPException(status_code=400, detail=f"File failed compliance check: {e}") from e
        except Exception as e:
            logger.error("Failed to probe file: %s", e)
            raise HTTPException(status_code=400, detail="We could not make sense of this file, sorry") from e

        try:
            await ingest.ingest(upload_meta.video_id, upload_file, metadata)
        except Exception as e:
            logger.error("Failed to ingest file: %s", e)
            return {}

    return {}
