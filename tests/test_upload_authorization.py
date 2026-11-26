"""Ingest asks django-api, as the uploader, whether an upload may start.

tusd asks before a byte is stored, so a refusal has to stop the upload
rather than surface once the file is already on disk.
"""

import pytest

from app.django_client.service import DjangoApiError

UPLOAD_PATH = "/api/videos/1234/upload"


@pytest.mark.asyncio
async def test_the_uploaders_credentials_are_sent_and_ingests_are_not(httpserver, django_api_service):
    httpserver.expect_request(UPLOAD_PATH, method="GET").respond_with_json({"uploadUrl": "x"})

    await django_api_service.authorize_upload("1234", {"Cookie": "sessionid=member"})

    request = httpserver.log[0][0]
    assert request.headers.get("Cookie") == "sessionid=member"
    # The fixture's client carries ingest's own token, "secret".
    assert "Authorization" not in request.headers


@pytest.mark.asyncio
async def test_an_api_token_is_forwarded_as_given(httpserver, django_api_service):
    httpserver.expect_request(UPLOAD_PATH, method="GET").respond_with_json({"uploadUrl": "x"})

    await django_api_service.authorize_upload("1234", {"Authorization": "Token member"})

    assert httpserver.log[0][0].headers.get("Authorization") == "Token member"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_refusal_raises_with_djangos_own_status(httpserver, django_api_service, status):
    httpserver.expect_request(UPLOAD_PATH, method="GET").respond_with_data("", status=status)

    with pytest.raises(DjangoApiError) as refusal:
        await django_api_service.authorize_upload("1234", {"Cookie": "sessionid=outsider"})

    assert refusal.value.status_code == status


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed_with_the_uploaders_cookie(httpserver, django_api_service):
    httpserver.expect_request(UPLOAD_PATH, method="GET").respond_with_data(
        "", status=302, headers={"Location": "https://elsewhere.example/"}
    )

    with pytest.raises(DjangoApiError) as refusal:
        await django_api_service.authorize_upload("1234", {"Cookie": "sessionid=member"})

    assert refusal.value.status_code == 302
