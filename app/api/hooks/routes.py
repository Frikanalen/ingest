import json
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends
from frikanalen_django_api_client.models import IngestStateEnum
from starlette.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app.api.hooks.metadata import (
    ComplianceError,
    MetadataExtractor,
    UploadKind,
    UploadMetaData,
    get_upload_metadata,
)
from app.api.hooks.schema.request import HookRequest
from app.api.hooks.schema.response import FileInfoChanges, HookResponse, HTTPResponse
from app.api.hooks.spool import IMAGE_UPLOAD_DIR, clear_abandoned_image_uploads
from app.archive_store import ArchiveStore
from app.django_client.service import DjangoApiError, DjangoApiService
from app.ingest import Ingester
from app.ingest_reporting import IngestErrorCode, IngestReporter
from app.program_image import MAX_IMAGE_BYTES, ImageComplianceError, ProgramImageIngester
from app.util.app_state import get_archive_store, get_django_api, get_metadata_extractor
from app.util.settings import IngestAppSettings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

IMAGE_UPLOAD_ID_PREFIX = "image"


FORWARDED_CREDENTIALS = ("Authorization", "Cookie")


def uploader_credentials(hook_request: HookRequest, settings: IngestAppSettings) -> dict[str, str]:
    """The credentials the uploader sent with the tus request.

    A browser upload carries the session cookie, since uploads go to the
    site's own origin; a script sends `Authorization: Token ...`. Both are
    passed on to django-api as they came, and never logged.

    A request from another origin is refused outright. SameSite keeps a
    foreign site's requests from carrying the cookie at all, but a sibling
    subdomain counts as the same site, and nothing on one of those has any
    business uploading on a visitor's behalf.
    """
    request = hook_request.event.http_request if hook_request.event else None
    headers = (request.header.root if request and request.header else None) or {}
    by_name = {name.lower(): values for name, values in headers.items()}

    origin = by_name.get("origin")
    if origin and origin[0] not in settings.upload_allowed_origins():
        logger.warning("Upload from foreign origin %s refused", origin[0])
        raise HTTPException(status_code=403, detail="Uploads are not accepted from this origin")

    credentials = {name: by_name[name.lower()][0] for name in FORWARDED_CREDENTIALS if by_name.get(name.lower())}
    if not credentials:
        raise HTTPException(status_code=401, detail="Sign in to upload")
    return credentials


async def authorize(
    hook_request: HookRequest,
    metadata: UploadMetaData,
    settings: IngestAppSettings,
    django_api: DjangoApiService,
) -> None:
    """Refuse the request unless its sender may upload to the video."""
    credentials = uploader_credentials(hook_request, settings)
    try:
        await django_api.authorize_upload(metadata.video_id, credentials)
    except DjangoApiError as error:
        logger.warning("%s for video %s refused (%s)", hook_request.type, metadata.video_id, error.status_code)
        raise HTTPException(status_code=error.status_code, detail="Not allowed to upload to this video") from error


def refusal(error: HTTPException, **reject: bool) -> HookResponse:
    """Tell tusd to turn the request down, with ingest's own status.

    A hook that answers with an error status only makes tusd answer 500,
    which tells the uploader nothing. Answering 200 with a rejection and
    the response to send is how tusd passes a refusal on as it was meant.
    """
    return HookResponse(
        HTTPResponse=HTTPResponse(
            StatusCode=error.status_code,
            Body=json.dumps({"detail": error.detail}),
            Header={"Content-Type": "application/json"},
        ),
        **reject,
    )


async def prepare_upload(
    hook_request: HookRequest,
    settings: IngestAppSettings,
    django_api: DjangoApiService,
) -> HookResponse:
    metadata = get_upload_metadata(hook_request)
    await authorize(hook_request, metadata, settings, django_api)

    sanitized_filename = secure_filename(metadata.orig_file_name)
    if not sanitized_filename:
        raise HTTPException(status_code=422, detail="Invalid upload filename")
    if metadata.upload_kind == UploadKind.PROGRAM_IMAGE:
        upload_size = hook_request.event.upload.size
        if upload_size is not None and upload_size > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image files may not exceed 10 MB")
        upload_id = f"{IMAGE_UPLOAD_ID_PREFIX}{uuid4().hex}"
        new_file = Path(metadata.video_id, IMAGE_UPLOAD_DIR, upload_id, sanitized_filename)
        # This upload gets a path of its own, so nothing will ever overwrite an
        # earlier one -- which is why the earlier ones that came to nothing are
        # collected here instead.
        clear_abandoned_image_uploads(settings.tusd_dir, metadata.video_id)
    else:
        # The ID is the upload's URL (/upload/<id>), and tusd lets whoever
        # holds that URL append to the upload or delete it. So it must not
        # be guessable, as the video ID would be; the spool path can still be
        # keyed on the video.
        upload_id = uuid4().hex
        new_file = Path(metadata.video_id, sanitized_filename)

    # A video's spool path is keyed on the video, so a second upload to the
    # same video lands on the first one's leftovers. Dropping them is the same
    # policy the archive applies a step later, where the new original
    # supersedes the old: an upload replaces what it arrives on top of.
    if metadata.upload_kind == UploadKind.VIDEO and (settings.tusd_dir / new_file).exists():
        logger.warning("File already exists, deleting!: %s", settings.tusd_dir / new_file)
        (settings.tusd_dir / new_file).unlink()

    logger.info(
        "Prepared %s upload for video %s at %s",
        metadata.upload_kind,
        metadata.video_id,
        new_file,
    )
    return HookResponse(ChangeFileInfo=FileInfoChanges(ID=upload_id, Storage={"Path": str(new_file)}))


async def ingest_program_image(
    hook_request: HookRequest,
    upload_meta: UploadMetaData,
    upload_file: Path,
    archive: ArchiveStore,
    django_api: DjangoApiService,
) -> None:
    assert upload_meta.image_role is not None
    upload_id = hook_request.event.upload.id or ""
    image_id = upload_id.removeprefix(IMAGE_UPLOAD_ID_PREFIX)
    if not upload_id.startswith(IMAGE_UPLOAD_ID_PREFIX) or not image_id.isalnum():
        raise HTTPException(status_code=422, detail="Invalid programme-image upload id")
    try:
        await ProgramImageIngester(archive=archive, django_api=django_api).ingest(
            video_id=upload_meta.video_id,
            image_id=image_id,
            role=upload_meta.image_role,
            uploaded_file=upload_file,
        )
    except ImageComplianceError as error:
        logger.warning("Programme image failed validation: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("Failed to archive programme image")
        raise HTTPException(status_code=500, detail="Could not archive image") from error


async def authorize_completion(
    hook_request: HookRequest, settings: IngestAppSettings, django_api: DjangoApiService
) -> HookResponse | dict:
    """Authorize the request that finishes or deletes an upload.

    Appending needs only the upload's URL, so these are checked again: a
    file whose last bytes came from someone not allowed to upload is not
    ingested, and nobody else may delete an upload. tusd has no way to
    reject a finish other than failing the hook, so for pre-finish this
    raises.
    """
    metadata = get_upload_metadata(hook_request)
    if hook_request.type == "pre-finish":
        await authorize(hook_request, metadata, settings, django_api)
        return {}
    try:
        await authorize(hook_request, metadata, settings, django_api)
    except HTTPException as error:
        return refusal(error, RejectTermination=True)
    return {}


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
        try:
            return await prepare_upload(hook_request, settings, django_api)
        except HTTPException as error:
            return refusal(error, RejectUpload=True)

    if hook_request.type in ("pre-finish", "pre-terminate"):
        return await authorize_completion(hook_request, settings, django_api)

    if hook_request.type == "post-finish":
        upload_meta = get_upload_metadata(hook_request)
        # eg. /upload/12345/original_video.mp4, as tusd sees it
        path_from_tus = Path(hook_request.event.upload.storage["Path"])
        # eg. ./upload/12345/original_video.mp4, as ingest sees it
        upload_file = settings.tusd_dir / path_from_tus.relative_to(settings.tusd_upload_dir)

        if upload_meta.upload_kind == UploadKind.PROGRAM_IMAGE:
            await ingest_program_image(hook_request, upload_meta, upload_file, archive, django_api)
            return {}

        ingest = Ingester(archive=archive, django_api=django_api)
        # One reporter for the whole run, handed to the Ingester below so that
        # the probe and the pipeline read as a single sequence of states.
        reporter = IngestReporter(django_api, upload_meta.video_id)

        await reporter.state(IngestStateEnum.PROBING)

        try:
            metadata = await metadata_extractor.assert_compliance(upload_file)
        except ComplianceError as e:
            logger.error("File failed compliance check: %s", e)
            await reporter.failed(IngestErrorCode.NOT_COMPLIANT, str(e))
            raise HTTPException(status_code=400, detail=f"File failed compliance check: {e}") from e
        except Exception as e:
            logger.error("Failed to probe file: %s", e)
            await reporter.failed(IngestErrorCode.UNREADABLE, str(e))
            raise HTTPException(status_code=400, detail="We could not make sense of this file, sorry") from e

        try:
            await ingest.ingest(upload_meta.video_id, upload_file, metadata, reporter)
        except Exception as e:
            # tusd has nowhere to put this: the browser considered the upload
            # finished several minutes ago. The report is the only thing that
            # reaches the person who sent the file, which is why swallowing the
            # exception here is now defensible and was not before.
            logger.error("Failed to ingest file: %s", e)
            await reporter.failed_unless_already(IngestErrorCode.INTERNAL_ERROR, str(e))
            return {}

    return {}
