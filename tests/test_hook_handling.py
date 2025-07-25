from fastapi.testclient import TestClient

from app.main import app
from app.tus_hook.hook_schema import FileInfo, Header, HookEvent, HookRequest, HTTPRequest, MetaData

hook_request = HookRequest(
    Type="pre-create",
    Event=HookEvent(
        Upload=(
            FileInfo.model_validate(
                {
                    "ID": "",
                    "Size": 3012,
                    "SizeIsDeferred": False,
                    "Offset": 0,
                    "MetaData": MetaData(),
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
good_pre_create_hook_request = hook_request.model_copy(deep=True)
good_pre_create_hook_request.event.upload.meta_data = MetaData(
    **dict(VideoID="1234", OrigFileName="test.mp4", UploadToken="asdfasdf")
)


def test_pre_create_fails_if_metadata_bad():
    client = TestClient(app)

    mock_hook_payload = hook_request.model_dump(by_alias=True)

    response = client.post("/", json=mock_hook_payload)
    assert response.status_code == 422


def test_pre_create_succeeds_if_metadata_parses():
    client = TestClient(app)
    mock_hook_payload = good_pre_create_hook_request.model_dump(by_alias=True)

    response = client.post("/", json=mock_hook_payload)
    assert response.status_code == 200


def test_post_create_fails_if_metadata_bad():
    client = TestClient(app)

    mock_hook_payload = hook_request.model_dump(by_alias=True)

    response = client.post("/", json=mock_hook_payload)
    assert response.status_code == 422


def test_post_finish_triggers_ingest(color_bars_video):
    client = TestClient(app)
    good_post_finish_hook_request = good_pre_create_hook_request.model_copy(deep=True)
    good_post_finish_hook_request.type = "post-finish"
    good_post_finish_hook_request.event.upload.meta_data = MetaData(
        **{"VideoID": "1234", "OrigFileName": "test.mp4", "UploadToken": "asdfasdf"}
    )
    good_post_finish_hook_request.event.upload.storage = {"Type": "filestore", "Path": str(color_bars_video)}

    mock_hook_payload = good_post_finish_hook_request.model_dump(by_alias=True)

    response = client.post("/", json=mock_hook_payload)
    assert response.status_code == 200
