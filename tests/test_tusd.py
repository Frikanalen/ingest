import requests
from tusclient import client
import io

from werkzeug import Response


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


def test_tusd_upload_hooks(tusd_server_with_hooks, mock_hook_server):
    my_client = client.TusClient(url=tusd_server_with_hooks.url)

    def success_response(request_data):
        return Response(
            status=200,
            response="{}",
        )

    mock_hook_server.configure_response(success_response)

    uploader = my_client.uploader(file_stream=(io.BytesIO(b"X" * 1337)), chunk_size=500)

    uploader.upload()

    assert uploader.offset == 1337
