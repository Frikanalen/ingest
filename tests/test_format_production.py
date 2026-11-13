"""What producing one format commits to.

The revision scheme rests on a single ordering guarantee: a videofile row is
only created once every file of that format is in the archive. That is what
lets a row's existence stand for "this landed in full", and what makes the
revision it carries safe to act on. These pin it.
"""

import shutil
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import VideoFileVariantEnum
from frikanalen_django_api_client.types import Response

from app.api.hooks.metadata import MetadataExtractor
from app.archive_store import ArchiveEntry, ArchiveSession
from app.django_client.service import DjangoApiError, DjangoApiService
from app.media.produce import FormatProducer, SourceMedia, TranscodeFailed

VIDEO_ID = "12345"

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


class OrderRecordingSession(ArchiveSession):
    """Remembers the order archive writes happened in."""

    def __init__(self):
        self.events: list[str] = []

    async def exists(self, destination: PurePosixPath) -> bool:
        return False

    async def put(self, source: Path, destination: PurePosixPath) -> None:
        self.events.append(f"put {destination}")

    async def list_dir(self, destination: PurePosixPath) -> list[ArchiveEntry]:
        raise NotImplementedError

    async def get(self, source: PurePosixPath, destination: Path) -> None:
        raise NotImplementedError

    async def trash(self, path: PurePosixPath) -> PurePosixPath:
        raise NotImplementedError

    async def delete_variant(self, variant: str, video_id: str) -> bool:
        raise NotImplementedError


def created(status: HTTPStatus = HTTPStatus.CREATED) -> Response:
    return Response(status_code=status, content=b"", headers={}, parsed=None)


@pytest.fixture
def archive() -> OrderRecordingSession:
    return OrderRecordingSession()


@pytest.fixture
def django_api(archive) -> AsyncMock:
    api = AsyncMock()

    async def note(**kwargs):
        archive.events.append(f"register {kwargs['filename']}")

    api.create_video_file.side_effect = note
    return api


@pytest.fixture
def producer(archive, django_api) -> FormatProducer:
    return FormatProducer(archive=archive, django_api=django_api)


@pytest_asyncio.fixture
async def source(color_bars_video) -> SourceMedia:
    metadata = await MetadataExtractor().do_probe(color_bars_video)
    return SourceMedia.probed(VIDEO_ID, color_bars_video, metadata)


@pytest.mark.asyncio
async def test_registers_the_revision_the_template_declared(producer, django_api, source, tmp_path):
    await producer.produce(source, VideoFileVariantEnum.LARGE_THUMB, tmp_path)

    [call] = django_api.create_video_file.call_args_list
    assert call.kwargs["profile_revision"] == 1
    assert call.kwargs["file_format"] == VideoFileVariantEnum.LARGE_THUMB


@pytest.mark.asyncio
async def test_registers_only_after_every_file_is_archived(producer, archive, source, tmp_path):
    """A row that appeared first would claim a format that is not there yet."""
    await producer.produce(source, VideoFileVariantEnum.LARGE_THUMB, tmp_path)

    assert archive.events[-1].startswith("register")
    assert any(event.startswith("put") for event in archive.events)


@pytest.mark.asyncio
async def test_returns_where_the_primary_output_landed(producer, source, tmp_path):
    destination = await producer.produce(source, VideoFileVariantEnum.MED_THUMB, tmp_path)

    assert destination == PurePosixPath(f"{VIDEO_ID}/med_thumb/{source.path.stem}.jpg")


@pytest.mark.asyncio
async def test_a_failing_encode_registers_nothing(producer, django_api, source, tmp_path):
    missing = SourceMedia.probed(VIDEO_ID, Path("/nonexistent/source.mp4"), source.metadata)

    with pytest.raises(TranscodeFailed):
        await producer.produce(missing, VideoFileVariantEnum.LARGE_THUMB, tmp_path)

    django_api.create_video_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_probed_notices_an_audio_track(source):
    """The colour bars fixture is silent, so no audio adaptation set is valid."""
    assert source.has_audio is False


@pytest.mark.asyncio
async def test_probed_notices_audio_when_there_is_some(color_bars_video_with_tone):
    metadata = await MetadataExtractor().do_probe(color_bars_video_with_tone)

    assert SourceMedia.probed(VIDEO_ID, color_bars_video_with_tone, metadata).has_audio is True


@pytest.mark.asyncio
async def test_create_video_file_sends_the_profile_revision(monkeypatch):
    captured = {}

    async def fake_create(*, client, body):
        captured["body"] = body
        return created()

    monkeypatch.setattr("app.django_client.service.videofiles_create.asyncio_detailed", fake_create)

    await DjangoApiService(client=None).create_video_file(
        filename=f"{VIDEO_ID}/dash/manifest.mpd",
        video_id=VIDEO_ID,
        file_format=VideoFileVariantEnum.DASH,
        profile_revision=3,
    )

    assert captured["body"].profile_revision == 3
    assert captured["body"].to_dict()["profileRevision"] == 3


@pytest.mark.asyncio
async def test_create_video_file_omits_the_revision_when_there_is_none(monkeypatch):
    """The original is not produced by a template, so it has no revision."""
    captured = {}

    async def fake_create(*, client, body):
        captured["body"] = body
        return created()

    monkeypatch.setattr("app.django_client.service.videofiles_create.asyncio_detailed", fake_create)

    await DjangoApiService(client=None).create_video_file(
        filename=f"{VIDEO_ID}/original/video.mp4",
        video_id=VIDEO_ID,
        file_format=VideoFileVariantEnum.ORIGINAL,
    )

    assert "profileRevision" not in captured["body"].to_dict()


@pytest.mark.asyncio
async def test_a_rebuild_re_points_the_row_it_already_has(producer, django_api, source, tmp_path):
    """One row per variant: a second would be refused, so the old one is taken over."""
    await producer.produce(source, VideoFileVariantEnum.LARGE_THUMB, tmp_path, row_id=77)

    django_api.create_video_file.assert_not_awaited()
    [call] = django_api.update_video_file.call_args_list
    assert call.args == (77,)
    assert call.kwargs["profile_revision"] == 1
    assert call.kwargs["filename"] == f"{VIDEO_ID}/large_thumb/{source.path.stem}.jpg"


@pytest.mark.asyncio
async def test_a_refused_registration_raises(monkeypatch):
    """A 400 is a documented response, so it parses rather than raising on its own.

    Swallowed, it let a rebuild that collided with an existing row report done
    while the catalogue still described the files it had just replaced.
    """

    async def refuse(*, client, body):
        return created(HTTPStatus.BAD_REQUEST)

    monkeypatch.setattr("app.django_client.service.videofiles_create.asyncio_detailed", refuse)

    with pytest.raises(DjangoApiError):
        await DjangoApiService(client=None).create_video_file(
            filename=f"{VIDEO_ID}/large_thumb/source.jpg",
            video_id=VIDEO_ID,
            file_format=VideoFileVariantEnum.LARGE_THUMB,
            profile_revision=1,
        )


@pytest.mark.asyncio
async def test_update_video_file_sends_the_new_filename_and_revision(monkeypatch):
    captured = {}

    async def fake_patch(file_id, *, client, body):
        captured["id"], captured["body"] = file_id, body
        return created(HTTPStatus.OK)

    monkeypatch.setattr("app.django_client.service.videofiles_partial_update.asyncio_detailed", fake_patch)

    await DjangoApiService(client=None).update_video_file(
        77, filename=f"{VIDEO_ID}/dash/manifest.mpd", profile_revision=2
    )

    assert captured["id"] == 77
    assert captured["body"].to_dict() == {"filename": f"{VIDEO_ID}/dash/manifest.mpd", "profileRevision": 2}
