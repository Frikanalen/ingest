import requests


def test_tusd_options(tusd_server):
    url = tusd_server["url"]

    response = requests.options(url)

    assert response.status_code == 200
    assert response.headers.get("Tus-Resumable") == "1.0.0"
    assert "Tus-Version" in response.headers
