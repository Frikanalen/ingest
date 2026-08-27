"""Ingest only accepts an upload django-api says was authorized.

tusd asks before a byte is stored, so a token django-api will not vouch
for has to stop the upload rather than surface once the file is already
on disk.
"""

import pytest

from app.django_client.service import DjangoApiError

VERIFY_PATH = "/api/videos/1234/upload_token/verify"


@pytest.mark.asyncio
async def test_a_verified_token_is_sent_under_the_name_django_reads(httpserver, django_api_service):
    httpserver.expect_request(VERIFY_PATH, method="POST").respond_with_data("", status=204)

    await django_api_service.verify_upload_token("1234", "s3cret")

    assert httpserver.log[0][0].json == {"uploadToken": "s3cret"}


@pytest.mark.asyncio
async def test_a_rejected_token_raises_with_djangos_own_status(httpserver, django_api_service):
    """django-api answers an invalid token exactly as it answers a missing video."""
    httpserver.expect_request(VERIFY_PATH, method="POST").respond_with_data("", status=404)

    with pytest.raises(DjangoApiError) as rejection:
        await django_api_service.verify_upload_token("1234", "wrong")

    assert rejection.value.status_code == 404
