from frikanalen_django_api_client import AuthenticatedClient
from starlette.requests import Request

from app.config import config


def get_client_from_app_state(request: Request) -> AuthenticatedClient:
    return request.app.state.app_state.api_client  # type: ignore[attr-defined]


def build_client():
    return AuthenticatedClient(
        base_url=config.django.base_url, token=config.django.token, raise_on_unexpected_status=True
    )
