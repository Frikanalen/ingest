import tempfile
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.get_settings import get_settings
from app.api.hooks.schema.request import FileInfo, Header, HookEvent, HookRequest, HTTPRequest, MetaData
from app.main import app
from app.util.app_state import get_django_api
from app.util.settings import ApiConfig, Settings

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

HOOK_PATH = "/tusdHooks/"


def get_settings_override():
    return Settings(
        api=ApiConfig(url="http://localhost:8000", username="", password=""),
        archive_dir=tempfile.gettempdir(),  # fixme: no cleanup here yet
        host="localhost",
        port=55025,
    )


app.dependency_overrides[get_settings] = get_settings_override
app.dependency_overrides[get_django_api] = lambda: AsyncMock()


def test_pre_create_fails_if_metadata_bad():
    pre_create_rq_missing_metadata = pre_create_request_valid.model_copy(deep=True)
    pre_create_rq_missing_metadata.event.upload.meta_data = MetaData()
    mock_hook_payload = pre_create_rq_missing_metadata.model_dump(by_alias=True)

    response = client.post(HOOK_PATH, json=mock_hook_payload)
    assert response.status_code == 422


def test_pre_create_succeeds_if_metadata_parses():
    mock_hook_payload = pre_create_request_valid.model_dump(by_alias=True)

    response = client.post(HOOK_PATH, json=mock_hook_payload)
    assert response.status_code == 200


def test_post_create_fails_if_metadata_bad():
    bad_request = pre_create_request_valid.model_copy(deep=True)
    bad_request.event.upload.meta_data = MetaData()

    response = client.post(HOOK_PATH, json=bad_request.model_dump(by_alias=True))
    assert response.status_code == 422
