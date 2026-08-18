import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends
from starlette.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app.api.hooks.metadata import ComplianceError, MetadataExtractor, get_upload_metadata
from app.api.hooks.schema.request import HookRequest
from app.api.hooks.schema.response import FileInfoChanges, HookResponse
from app.archive_store import ArchiveStore
from app.django_client.service import DjangoApiService
from app.ingest import Ingester
from app.util.app_state import get_archive_store, get_django_api, get_metadata_extractor
from app.util.settings import IngestAppSettings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/")
async def receive_hook(
    hook_request: HookRequest,
    settings: IngestAppSettings = Depends(get_settings),
    django_api: DjangoApiService = Depends(get_django_api),
    archive: ArchiveStore = Depends(get_archive_store),
    metadata_extractor: MetadataExtractor = Depends(get_metadata_extractor),
):
    logger.info("Received hook: %s", hook_request.type)
    if hook_request.type == "pre-create":
        # read and validate request metadata
        metadata = get_upload_metadata(hook_request)
        try:
            await django_api.verify_upload_token(metadata.video_id, metadata.upload_token)
        except httpx.HTTPStatusError as e:
            logger.warning("Upload token verification failed for video %s", metadata.video_id)
            raise HTTPException(status_code=e.response.status_code, detail="Invalid upload token") from e

        # construct updated values for the file info
        sanitized_filename = secure_filename(metadata.orig_file_name)
        upload_id = f"{metadata.video_id}"
        new_file = Path(f"{upload_id}/{sanitized_filename}")

        if (settings.tusd_dir / new_file).exists():
            logger.warning("File already exists, deleting!: %s", (settings.tusd_dir / new_file))
            (settings.tusd_dir / new_file).unlink()

        return HookResponse(ChangeFileInfo=FileInfoChanges(ID=upload_id, Storage={"Path": str(new_file)}))

    if hook_request.type == "post-finish":
        ingest = Ingester(archive=archive, django_api=django_api, work_dir=settings.work_dir)
        upload_meta = get_upload_metadata(hook_request)
        # eg. /upload/12345/original_video.mp4, as tusd sees it
        path_from_tus = Path(hook_request.event.upload.storage["Path"])
        # eg. ./upload/12345/original_video.mp4, as ingest sees it
        upload_file = settings.tusd_dir / path_from_tus.relative_to(settings.tusd_upload_dir)

        try:
            metadata = await metadata_extractor.assert_compliance(upload_file)
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
