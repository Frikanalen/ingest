from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.archive_store import LocalArchiveStore
from app.program_image import ImageComplianceError, ProgramImageIngester, inspect_program_image


def write_image(path, *, image_format="PNG", size=(640, 360)):
    Image.new("RGB", size, color=(20, 80, 140)).save(path, format=image_format)
    return path


def test_inspection_reads_format_and_dimensions_from_the_file(tmp_path):
    uploaded = write_image(tmp_path / "misleading-name.jpg", image_format="PNG")

    metadata = inspect_program_image(uploaded)

    assert metadata.media_type == "image/png"
    assert metadata.extension == ".png"
    assert (metadata.width, metadata.height) == (640, 360)


def test_inspection_rejects_a_non_image(tmp_path):
    uploaded = tmp_path / "not-really.png"
    uploaded.write_text("plain text")

    with pytest.raises(ImageComplianceError, match="readable image"):
        inspect_program_image(uploaded)


@pytest.mark.asyncio
async def test_image_is_archived_before_django_records_it(tmp_path):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    uploaded = write_image(tmp_path / "key-art.png")
    django_api = AsyncMock()

    await ProgramImageIngester(archive=LocalArchiveStore(archive_root), django_api=django_api).ingest(
        video_id="1234",
        image_id="2f92e90dbb444e67bdb0893b5fe1d697",
        role="key_art_titled",
        uploaded_file=uploaded,
    )

    destination = archive_root / "1234/images/2f92e90dbb444e67bdb0893b5fe1d697.png"
    assert destination.is_file()
    assert not uploaded.exists()
    django_api.create_program_image.assert_awaited_once_with(
        video_id="1234",
        role="key_art_titled",
        filename="1234/images/2f92e90dbb444e67bdb0893b5fe1d697.png",
        media_type="image/png",
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
        "role": "episode_still",
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
