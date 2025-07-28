from typing import cast

from starlette.requests import Request

from app.util.ingest_app_state import IngestAppState


def get_app_state(request: Request) -> IngestAppState:
    return cast(IngestAppState, request.app.state.app_state)
