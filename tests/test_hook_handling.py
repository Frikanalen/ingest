import json
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
                            "Cookie": ["sessionid=member-session"],
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


def refused_with(response) -> int | None:
    """The status tusd is told to refuse the upload with, if it is."""
    assert response.status_code == 200, response.text
    body = response.json()
    if not body.get("RejectUpload"):
        return None
    return body["HTTPResponse"]["StatusCode"]


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
    assert refused_with(response) == 422


def test_pre_create_succeeds_if_metadata_parses():
    django_api.reset_mock()
    mock_hook_payload = pre_create_request_valid.model_dump(by_alias=True)

    response = client.post(HOOK_PATH, json=mock_hook_payload)
    assert response.status_code == 200
    django_api.authorize_upload.assert_awaited_once_with("1234", {"Cookie": "sessionid=member-session"})


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

    assert refused_with(response) == 413


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

    assert refused_with(response) == 422


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

    assert refused_with(response) == 422


def test_pre_create_forwards_djangos_refusal():
    django_api.authorize_upload.side_effect = DjangoApiError(403)

    response = client.post(HOOK_PATH, json=pre_create_request_valid.model_dump(by_alias=True))

    assert refused_with(response) == 403
    assert json.loads(response.json()["HTTPResponse"]["Body"]) == {"detail": "Not allowed to upload to this video"}
    django_api.authorize_upload.side_effect = None


def _with_headers(headers: dict[str, list[str]]) -> dict:
    request = pre_create_request_valid.model_copy(deep=True)
    request.event.http_request.header = Header.model_validate(headers)
    return request.model_dump(by_alias=True)


def test_pre_create_without_credentials_asks_the_uploader_to_sign_in():
    django_api.reset_mock()

    response = client.post(HOOK_PATH, json=_with_headers({"Tus-Resumable": ["1.0.0"]}))

    assert refused_with(response) == 401
    django_api.authorize_upload.assert_not_awaited()


def test_pre_create_forwards_an_api_token():
    django_api.reset_mock()

    response = client.post(HOOK_PATH, json=_with_headers({"Authorization": ["Token abc123"]}))

    assert response.status_code == 200
    django_api.authorize_upload.assert_awaited_once_with("1234", {"Authorization": "Token abc123"})


def test_pre_create_accepts_the_sites_own_origin():
    django_api.reset_mock()

    response = client.post(
        HOOK_PATH,
        json=_with_headers({"Cookie": ["sessionid=x"], "Origin": ["http://localhost:8000"]}),
    )

    assert response.status_code == 200


def test_pre_create_refuses_a_foreign_origin():
    django_api.reset_mock()

    response = client.post(
        HOOK_PATH,
        json=_with_headers({"Cookie": ["sessionid=x"], "Origin": ["https://evil.example"]}),
    )

    assert refused_with(response) == 403
    django_api.authorize_upload.assert_not_awaited()


def test_pre_create_still_accepts_a_legacy_upload_token_field():
    django_api.reset_mock()

    response = client.post(HOOK_PATH, json=pre_create_request_valid.model_dump(by_alias=True))

    assert response.status_code == 200


def test_post_create_fails_if_metadata_bad():
    bad_request = pre_create_request_valid.model_copy(deep=True)
    bad_request.event.upload.meta_data = MetaData()

    response = client.post(HOOK_PATH, json=bad_request.model_dump(by_alias=True))
    assert refused_with(response) == 422


def test_a_video_upload_gets_an_unguessable_id_and_a_video_keyed_path():
    django_api.reset_mock()

    first = client.post(HOOK_PATH, json=pre_create_request_valid.model_dump(by_alias=True))
    second = client.post(HOOK_PATH, json=pre_create_request_valid.model_dump(by_alias=True))

    first_change = first.json()["ChangeFileInfo"]
    assert first_change["ID"] != "1234"
    assert len(first_change["ID"]) == 32
    assert first_change["ID"] != second.json()["ChangeFileInfo"]["ID"]
    assert first_change["Storage"]["Path"] == "1234/test.mp4"


def _as(hook_type: str, headers: dict[str, list[str]]) -> dict:
    request = pre_create_request_valid.model_copy(deep=True)
    request.type = hook_type
    request.event.http_request.header = Header.model_validate(headers)
    return request.model_dump(by_alias=True)


def test_pre_finish_from_an_allowed_uploader_goes_ahead():
    django_api.reset_mock()

    response = client.post(HOOK_PATH, json=_as("pre-finish", {"Cookie": ["sessionid=member"]}))

    assert response.status_code == 200
    django_api.authorize_upload.assert_awaited_once_with("1234", {"Cookie": "sessionid=member"})


def test_pre_finish_without_credentials_fails_the_hook():
    """tusd's only way to stop a finish: post-finish never runs."""
    django_api.reset_mock()

    response = client.post(HOOK_PATH, json=_as("pre-finish", {"Tus-Resumable": ["1.0.0"]}))

    assert response.status_code == 401


def test_pre_finish_refused_by_django_fails_the_hook():
    django_api.authorize_upload.side_effect = DjangoApiError(403)
    try:
        response = client.post(HOOK_PATH, json=_as("pre-finish", {"Cookie": ["sessionid=outsider"]}))
    finally:
        django_api.authorize_upload.side_effect = None

    assert response.status_code == 403


def test_pre_terminate_by_an_outsider_is_rejected():
    django_api.authorize_upload.side_effect = DjangoApiError(403)
    try:
        response = client.post(HOOK_PATH, json=_as("pre-terminate", {"Cookie": ["sessionid=outsider"]}))
    finally:
        django_api.authorize_upload.side_effect = None

    assert response.status_code == 200
    assert response.json()["RejectTermination"] is True
    assert response.json()["HTTPResponse"]["StatusCode"] == 403


def test_pre_terminate_by_the_uploader_goes_ahead():
    django_api.reset_mock()

    response = client.post(HOOK_PATH, json=_as("pre-terminate", {"Cookie": ["sessionid=member"]}))

    assert response.status_code == 200
    assert not response.json().get("RejectTermination")
