import asyncio
import warnings
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path

from frikanalen_django_api_client.models import MediaTypeEnum, RoleEnum
from PIL import Image, UnidentifiedImageError

from app.archive_store import ArchiveStore
from app.django_client.service import DjangoApiService
from app.util.file_name_utils import program_image_location

logger = getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 65_535

IMAGE_FORMATS = {
    "JPEG": (MediaTypeEnum.IMAGEJPEG, ".jpg"),
    "PNG": (MediaTypeEnum.IMAGEPNG, ".png"),
    "WEBP": (MediaTypeEnum.IMAGEWEBP, ".webp"),
}


class ImageComplianceError(ValueError):
    """The uploaded file is not a supported, safe still image."""


@dataclass(frozen=True)
class ProgramImageMetadata:
    media_type: MediaTypeEnum
    extension: str
    width: int
    height: int


def inspect_program_image(path: Path) -> ProgramImageMetadata:
    """What the archive needs to know about an upload, read from the bytes.

    The size check is inside the try so that an upload which is no longer there
    -- because a previous attempt found it non-compliant and cleared it -- gets
    the same answer a retry deserves: not compliant, rather than an unhandled
    OSError that reads as ingest having broken.
    """
    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ImageComplianceError("Image files may not exceed 10 MB")

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image_format = image.format or ""
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError) as error:
        raise ImageComplianceError("The upload is not a readable image") from error

    try:
        media_type, extension = IMAGE_FORMATS[image_format]
    except KeyError as error:
        raise ImageComplianceError("Only JPEG, PNG and WebP images are supported") from error
    if frames != 1:
        raise ImageComplianceError("Animated images are not supported")
    if not 0 < width <= MAX_IMAGE_DIMENSION or not 0 < height <= MAX_IMAGE_DIMENSION:
        raise ImageComplianceError("Image dimensions may not exceed 65535 pixels")

    return ProgramImageMetadata(
        media_type=media_type,
        extension=extension,
        width=width,
        height=height,
    )


class ProgramImageIngester:
    def __init__(self, archive: ArchiveStore, django_api: DjangoApiService):
        self.archive = archive
        self.django_api = django_api

    async def ingest(
        self,
        *,
        video_id: str,
        image_id: str,
        role: RoleEnum,
        uploaded_file: Path,
    ) -> None:
        try:
            metadata = await asyncio.to_thread(inspect_program_image, uploaded_file)
        except ImageComplianceError:
            # The one failure that is final. Every other way this can go wrong
            # -- the archive unreachable, Django refusing the registration --
            # leaves work a retry can finish, which is why the file survives
            # them; nothing will ever make these bytes a valid image, so
            # keeping them only fills the upload volume that pins this pod to
            # one replica.
            uploaded_file.unlink(missing_ok=True)
            raise

        destination = program_image_location(video_id, image_id, metadata.extension)

        async with self.archive.open() as archive:
            # A post-finish hook may be retried after publication but before
            # Django acknowledged registration. Reuse that exact destination
            # rather than either overwriting it or creating a duplicate.
            #
            # Deliberately not what a video upload does, which supersedes what
            # it finds. The difference is what the destination is keyed on: an
            # image gets a fresh id per upload, so something already at this
            # exact path can only be this same upload arriving twice. A video's
            # path is keyed on the video, so what is there may equally be a
            # different file the member is replacing -- and exists() cannot
            # tell the two apart. See Ingester._supersede_previous_media.
            if not await archive.exists(destination):
                await archive.put(uploaded_file, destination)
            else:
                logger.info("Programme image %s is already archived; retrying registration", destination)

        await self.django_api.create_program_image(
            video_id=video_id,
            role=role,
            filename=str(destination),
            media_type=metadata.media_type,
            width=metadata.width,
            height=metadata.height,
        )
        uploaded_file.unlink()
        logger.info(
            "Registered programme image %s for video %s as %s (%dx%d)",
            destination,
            video_id,
            role,
            metadata.width,
            metadata.height,
        )
