import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.hooks.schema.request import FileInfo, Header, HookEvent, HookRequest, HTTPRequest, MetaData
from app.archive_store import LocalArchiveStore
from app.django_client.service import DjangoApiError
from app.main import app
from app.util.app_state import get_archive_store, get_django_api
from app.util.settings import DjangoApiSettingsPwdAuth, IngestAppSettings, LocalArchiveSettings, get_settings

pre_create_request_valid = HookRequest(
    Type="pre-create",
    Event=HookEvent(
        Upload=(
            FileInfo.model_validate(
                {
                    "ID": "",
                    "Size": 3012,
                    "SizeIsDeferred": False,
                    "Offset": 0,
                    "MetaData": MetaData(**{"videoID": "1234", "origFileName": "test.mp4", "uploadToken": "asdfasdf"}),
                    "IsPartial": False,
                    "IsFinal": False,
                    "PartialUploads": None,
                    "Storage": None,
                },
            )
        ),
        HTTPRequest=(
            HTTPRequest.model_validate(
                {
                    "Method": "POST",
                    "URI": "/files/",
                    "RemoteAddr": "[::1]:36444",
                    "Header": Header.model_validate(
                        {
                            "Accept": ["*/*"],
                            "Accept-Encoding": ["gzip, deflate"],
                            "Connection": ["keep-alive"],
                            "Content-Length": ["0"],
                            "Host": ["localhost:55025"],
                            "Tus-Resumable": ["1.0.0"],
                            "Upload-Length": ["3012"],
                            "Upload-Metadata": [""],
                            "User-Agent": ["python-requests/2.32.4"],
                        }
                    ),
                }
            )
        ),
    ),
)


client = TestClient(app)
django_api = AsyncMock()

HOOK_PATH = "/tusdHooks/"


def get_settings_override():
    return IngestAppSettings(
        api=DjangoApiSettingsPwdAuth(url="http://localhost:8000", username="", password=""),
        tusd_dir=tempfile.gettempdir(),
        archive=LocalArchiveSettings(dir=tempfile.gettempdir()),  # fixme: no cleanup here yet
        host="localhost",
        port=55025,
    )


app.dependency_overrides[get_settings] = get_settings_override
app.dependency_overrides[get_django_api] = lambda: django_api
app.dependency_overrides[get_archive_store] = lambda: LocalArchiveStore(Path(tempfile.gettempdir()))


def test_pre_create_fails_if_metadata_bad():
    pre_create_rq_missing_metadata = pre_create_request_valid.model_copy(deep=True)
    pre_create_rq_missing_metadata.event.upload.meta_data = MetaData()
    mock_hook_payload = pre_create_rq_missing_metadata.model_dump(by_alias=True)

    response = client.post(HOOK_PATH, json=mock_hook_payload)
    assert response.status_code == 422


def test_pre_create_succeeds_if_metadata_parses():
    django_api.reset_mock()
    mock_hook_payload = pre_create_request_valid.model_dump(by_alias=True)

    response = client.post(HOOK_PATH, json=mock_hook_payload)
    assert response.status_code == 200
    django_api.verify_upload_token.assert_awaited_once_with("1234", "asdfasdf")


def test_pre_create_gives_each_programme_image_a_unique_spool_path():
    django_api.reset_mock()
    request = pre_create_request_valid.model_copy(deep=True)
    request.event.upload.meta_data = MetaData(
        **{
            "videoID": "1234",
            "origFileName": "Key art.png",
            "uploadToken": "asdfasdf",
            "uploadKind": "program_image",
            "imageRole": "key_art_titled",
        }
    )

    first = client.post(HOOK_PATH, json=request.model_dump(by_alias=True))
    second = client.post(HOOK_PATH, json=request.model_dump(by_alias=True))

    assert first.status_code == 200
    assert second.status_code == 200
    first_change = first.json()["ChangeFileInfo"]
    second_change = second.json()["ChangeFileInfo"]
    assert first_change["ID"].startswith("image")
    assert first_change["ID"] != second_change["ID"]
    assert first_change["Storage"]["Path"].startswith(f"1234/image_uploads/{first_change['ID']}/")
    assert first_change["Storage"]["Path"].endswith("/Key_art.png")


def test_pre_create_clears_this_videos_abandoned_image_uploads(tmp_path):
    """Nothing else ever will: each image upload gets a path of its own, so no
    later attempt overwrites one that failed."""
    django_api.reset_mock()
    abandoned = tmp_path / "1234" / "image_uploads" / "imageabandoned"
    abandoned.mkdir(parents=True)
    (abandoned / "rejected.png").write_text("not an image")
    long_ago = time.time() - 30 * 24 * 60 * 60
    for path in (abandoned / "rejected.png", abandoned):
        os.utime(path, (long_ago, long_ago))

    request = pre_create_request_valid.model_copy(deep=True)
    request.event.upload.meta_data = MetaData(
        **{
            "videoID": "1234",
            "origFileName": "Key art.png",
            "uploadToken": "asdfasdf",
            "uploadKind": "program_image",
            "imageRole": "key_art_titled",
        }
    )

    app.dependency_overrides[get_settings] = lambda: get_settings_override().model_copy(update={"tusd_dir": tmp_path})
    try:
        response = client.post(HOOK_PATH, json=request.model_dump(by_alias=True))
    finally:
        app.dependency_overrides[get_settings] = get_settings_override

    assert response.status_code == 200
    assert not abandoned.exists()


def test_pre_create_rejects_an_oversized_programme_image():
    request = pre_create_request_valid.model_copy(deep=True)
    request.event.upload.size = 10 * 1024 * 1024 + 1
    request.event.upload.meta_data = MetaData(
        **{
            "videoID": "1234",
            "origFileName": "huge.png",
            "uploadToken": "asdfasdf",
            "uploadKind": "program_image",
            "imageRole": "show_still",
        }
    )

    response = client.post(HOOK_PATH, json=request.model_dump(by_alias=True))

    assert response.status_code == 413


def test_pre_create_rejects_a_programme_image_without_a_role():
    request = pre_create_request_valid.model_copy(deep=True)
    request.event.upload.meta_data = MetaData(
        **{
            "videoID": "1234",
            "origFileName": "unclassified.png",
            "uploadToken": "asdfasdf",
            "uploadKind": "program_image",
        }
    )

    response = client.post(HOOK_PATH, json=request.model_dump(by_alias=True))

    assert response.status_code == 422


def test_pre_create_rejects_a_non_numeric_video_id():
    request = pre_create_request_valid.model_copy(deep=True)
    request.event.upload.meta_data = MetaData(
        **{
            "videoID": "../archive",
            "origFileName": "key-art.png",
            "uploadToken": "asdfasdf",
            "uploadKind": "program_image",
            "imageRole": "key_art_titled",
        }
    )

    response = client.post(HOOK_PATH, json=request.model_dump(by_alias=True))

    assert response.status_code == 422


def test_pre_create_forwards_upload_token_rejection():
    django_api.verify_upload_token.side_effect = DjangoApiError(404)

    response = client.post(HOOK_PATH, json=pre_create_request_valid.model_dump(by_alias=True))

    assert response.status_code == 404
    assert response.json() == {"detail": "Invalid upload token"}
    django_api.verify_upload_token.side_effect = None


def test_post_create_fails_if_metadata_bad():
    bad_request = pre_create_request_valid.model_copy(deep=True)
    bad_request.event.upload.meta_data = MetaData()

    response = client.post(HOOK_PATH, json=bad_request.model_dump(by_alias=True))
    assert response.status_code == 422
