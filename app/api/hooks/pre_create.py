import logging

from werkzeug.utils import secure_filename

from app.api.hooks.metadata import get_upload_metadata
from app.api.hooks.schema.request import HookRequest
from app.api.hooks.schema.response import FileInfoChanges, HookResponse

logger = logging.getLogger(__name__)


async def pre_create(hook_request: HookRequest):
    metadata = get_upload_metadata(hook_request)
    sanitized_filename = secure_filename(metadata.orig_file_name)
    upload_id = f"{metadata.video_id}"
    new_path = f"/upload/{upload_id}/{sanitized_filename}"

    return HookResponse(ChangeFileInfo=FileInfoChanges(ID=upload_id, Storage={"Path": new_path}))
