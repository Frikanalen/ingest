import logging

from werkzeug.utils import secure_filename

from app.api.hooks.metadata import get_upload_metadata
from app.api.hooks.schema import HookRequest

logger = logging.getLogger(__name__)


async def pre_create(hook_request: HookRequest):
    metadata = get_upload_metadata(hook_request)
    upload_id = f"{metadata.video_id}"
    return {
        "ChangeFileInfo": {
            "ID": upload_id,
            "Storage": {
                "Path": f"/upload/{upload_id}/{secure_filename(metadata.orig_file_name)}",
            },
        }
    }
