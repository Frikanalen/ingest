import requests
from tusclient import client
import io


def test_tusd_options(tusd_server):
    url = tusd_server["url"]

    response = requests.options(url)

    assert response.status_code == 200
    assert response.headers.get("Tus-Resumable") == "1.0.0"
    assert "Tus-Version" in response.headers


def test_tusd_upload(tusd_server):
    my_client = client.TusClient(url=tusd_server["url"])
    data = b"some bytes\n" * 500
    fs = io.BytesIO(data)

    uploader = my_client.uploader(file_stream=fs, chunk_size=200)

    uploader.upload()

    assert uploader.offset == len(data)
