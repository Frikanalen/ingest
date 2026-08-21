import asyncio
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.archive_store import ArchiveStore
from app.django_client.service import DjangoApiService
from app.util.file_name_utils import program_image_location

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 65_535

IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


class ImageComplianceError(ValueError):
    """The uploaded file is not a supported, safe still image."""


@dataclass(frozen=True)
class ProgramImageMetadata:
    media_type: str
    extension: str
    width: int
    height: int


def inspect_program_image(path: Path) -> ProgramImageMetadata:
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ImageComplianceError("Image files may not exceed 10 MB")

    try:
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
        role: str,
        uploaded_file: Path,
    ) -> None:
        metadata = await asyncio.to_thread(inspect_program_image, uploaded_file)
        destination = program_image_location(video_id, image_id, metadata.extension)

        async with self.archive.open() as archive:
            # A post-finish hook may be retried after publication but before
            # Django acknowledged registration. Reuse that exact destination
            # rather than either overwriting it or creating a duplicate.
            if not await archive.exists(destination):
                await archive.put(uploaded_file, destination)

        await self.django_api.create_program_image(
            video_id=video_id,
            role=role,
            filename=str(destination),
            media_type=metadata.media_type,
            width=metadata.width,
            height=metadata.height,
        )
        uploaded_file.unlink()
