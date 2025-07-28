import io

import requests
from tusclient import client

from app.api.hooks import UploadMetaData


def test_tusd_options(tusd_server_with_hooks):
    url = tusd_server_with_hooks.url

    response = requests.options(url)

    assert response.status_code == 200
    assert response.headers.get("Tus-Resumable") == "1.0.0"
    assert "Tus-Version" in response.headers


def test_tusd_upload(tusd_server):
    my_client = client.TusClient(url=tusd_server.url)

    uploader = my_client.uploader(file_stream=(io.BytesIO(b"X" * 1337)), chunk_size=500)

    uploader.upload()

    assert uploader.offset == 1337


def test_tusd_upload_hooks(tusd_server_with_hooks, start_fastapi_server, color_bars_video):
    my_client = client.TusClient(url=tusd_server_with_hooks.url)

    with color_bars_video.open("rb") as f:
        uploader = my_client.uploader(
            file_stream=f,
            chunk_size=500,
            metadata=UploadMetaData(VideoID="12345", OrigFileName="orig_file_name.mov", UploadToken="asdf").model_dump(
                by_alias=True
            ),
        )

        uploader.upload()

        assert uploader.offset == color_bars_video.stat().st_size
