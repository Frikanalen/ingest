from unittest.mock import AsyncMock

import pytest
from frikanalen_django_api_client.models import MediaTypeEnum, RoleEnum
from PIL import Image

from app.archive_store import LocalArchiveStore
from app.django_client.service import DjangoApiError
from app.program_image import ImageComplianceError, ProgramImageIngester, inspect_program_image

REGISTERED_IMAGE = {
    "id": 7,
    "video": 1234,
    "role": "key_art_titled",
    "filename": "1234/images/abc123.png",
    "mediaType": "image/png",
    "width": 1200,
    "height": 675,
    "url": "https://media.frikanalen.no/1234/images/abc123.png",
    "createdTime": "2026-08-27T10:00:00Z",
}


def write_image(path, *, image_format="PNG", size=(640, 360)):
    Image.new("RGB", size, color=(20, 80, 140)).save(path, format=image_format)
    return path


def test_inspection_reads_format_and_dimensions_from_the_file(tmp_path):
    uploaded = write_image(tmp_path / "misleading-name.jpg", image_format="PNG")

    metadata = inspect_program_image(uploaded)

    assert metadata.media_type == MediaTypeEnum.IMAGEPNG
    assert metadata.extension == ".png"
    assert (metadata.width, metadata.height) == (640, 360)


def test_inspection_rejects_a_non_image(tmp_path):
    uploaded = tmp_path / "not-really.png"
    uploaded.write_text("plain text")

    with pytest.raises(ImageComplianceError, match="readable image"):
        inspect_program_image(uploaded)


@pytest.mark.asyncio
async def test_django_registration_uses_the_videos_nested_image_collection(httpserver, django_api_service):
    httpserver.expect_request("/api/videos/1234/images", method="POST").respond_with_json(REGISTERED_IMAGE, status=201)

    await django_api_service.create_program_image(
        video_id="1234",
        role=RoleEnum.KEY_ART_TITLED,
        filename="1234/images/abc123.png",
        media_type=MediaTypeEnum.IMAGEPNG,
        width=1200,
        height=675,
    )

    request = httpserver.log[0][0]
    assert request.json == {
        "role": "key_art_titled",
        "filename": "1234/images/abc123.png",
        "mediaType": "image/png",
        "width": 1200,
        "height": 675,
    }


@pytest.mark.asyncio
async def test_a_registration_django_refuses_is_raised(httpserver, django_api_service):
    """A 400 is a documented response, so the client hands it back rather than raising."""
    httpserver.expect_request("/api/videos/1234/images", method="POST").respond_with_json(
        {
            "type": "validation_error",
            "errors": [{"code": "required", "detail": "This field is required.", "attr": "filename"}],
        },
        status=400,
    )

    with pytest.raises(DjangoApiError) as rejection:
        await django_api_service.create_program_image(
            video_id="1234",
            role=RoleEnum.KEY_ART_TITLED,
            filename="",
            media_type=MediaTypeEnum.IMAGEPNG,
            width=1200,
            height=675,
        )

    assert rejection.value.status_code == 400


@pytest.mark.asyncio
async def test_image_is_archived_before_django_records_it(tmp_path):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    uploaded = write_image(tmp_path / "key-art.png")
    django_api = AsyncMock()

    await ProgramImageIngester(archive=LocalArchiveStore(archive_root), django_api=django_api).ingest(
        video_id="1234",
        image_id="2f92e90dbb444e67bdb0893b5fe1d697",
        role=RoleEnum.KEY_ART_TITLED,
        uploaded_file=uploaded,
    )

    destination = archive_root / "1234/images/2f92e90dbb444e67bdb0893b5fe1d697.png"
    assert destination.is_file()
    assert not uploaded.exists()
    django_api.create_program_image.assert_awaited_once_with(
        video_id="1234",
        role=RoleEnum.KEY_ART_TITLED,
        filename="1234/images/2f92e90dbb444e67bdb0893b5fe1d697.png",
        media_type=MediaTypeEnum.IMAGEPNG,
        width=640,
        height=360,
    )


@pytest.mark.asyncio
async def test_retry_registers_an_already_published_image_without_overwriting_it(tmp_path):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    uploaded = write_image(tmp_path / "episode.png")
    django_api = AsyncMock()
    django_api.create_program_image.side_effect = RuntimeError("Django unavailable")
    ingester = ProgramImageIngester(archive=LocalArchiveStore(archive_root), django_api=django_api)
    arguments = {
        "video_id": "1234",
        "image_id": "ebcd6e0ff0aa4699a100550e347fc56d",
        "role": RoleEnum.EPISODE_STILL,
        "uploaded_file": uploaded,
    }

    with pytest.raises(RuntimeError, match="Django unavailable"):
        await ingester.ingest(**arguments)

    destination = archive_root / "1234/images/ebcd6e0ff0aa4699a100550e347fc56d.png"
    published = destination.read_bytes()
    assert uploaded.exists()

    django_api.create_program_image.side_effect = None
    await ingester.ingest(**arguments)

    assert destination.read_bytes() == published
    assert not uploaded.exists()
