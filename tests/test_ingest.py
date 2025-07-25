from unittest.mock import AsyncMock

import pytest

from app.archive import Archive
from app.converter import Converter
from app.django_api.service import DjangoApiService
from app.ingest import Ingester
from runner import Runner


@pytest.mark.asyncio
async def test_ingest_runs(color_bars_video):
    django_api = AsyncMock(spec=DjangoApiService)
    ingester = Ingester(
        "1234", django_api=django_api, converter_service=Converter(django_api, Runner()), archive=Archive()
    )
    result = await ingester.ingest(color_bars_video)

    assert result is not None  # Replace with actual assertions based on the ingest function's behavior
    assert isinstance(result, dict)  # Assuming ingest returns a dictionary, adjust as necessary
    assert "status" in result  # Example assertion, adjust based on expected output
