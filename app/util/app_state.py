from typing import cast

from fastapi import Depends
from starlette.requests import Request

from app.api.hooks.metadata import MetadataExtractor
from app.django_client.service import DjangoApiService
from app.util.ingest_app_state import IngestAppState


def get_app_state(request: Request) -> IngestAppState:
    return cast(IngestAppState, request.app.state.app_state)


def get_django_api(state=Depends(get_app_state)) -> DjangoApiService:
    return state.django_api


def get_metadata_extractor() -> MetadataExtractor:
    return MetadataExtractor()
