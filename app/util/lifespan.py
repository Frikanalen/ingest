import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from frikanalen_django_api_client import AuthenticatedClient, Client
from frikanalen_django_api_client.api.obtain_token_v2 import obtain_token_v2_create
from frikanalen_django_api_client.models import AuthTokenRequest

from app.api.debug.watch_folder.watcher import stop_observer
from app.django_client.service import DjangoApiService
from app.util.ingest_app_state import IngestAppState
from app.util.settings import settings

logger = logging.getLogger(__name__)


def api_get_key(username: str, password: str) -> str:
    """
    Helper function to obtain an API key using the provided username and password.
    This is used to authenticate the client with the Django API service.
    """
    logger.info("Obtaining API key for user: %s", username)
    login_client = Client(
        raise_on_unexpected_status=True,
        follow_redirects=True,
        base_url=str(settings.api.url),
    )
    response = obtain_token_v2_create.sync_detailed(body=(AuthTokenRequest(username, password)), client=login_client)

    return response.parsed.token


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler for the FastAPI app. Initializes the authenticated HTTP client
    for the DjangoAPI service and manages its lifecycle cleanly.
    """
    async with AsyncExitStack() as stack:
        token = api_get_key(
            settings.api.username,
            settings.api.password.get_secret_value(),
        )

        client = AuthenticatedClient(
            base_url=str(settings.api.url),
            token=token,
            raise_on_unexpected_status=True,
            follow_redirects=True,
        )

        django_api = DjangoApiService(client)
        await stack.enter_async_context(client)

        app.state.app_state = IngestAppState(django_api=django_api)  # type: ignore[attr-defined]

        yield  # App runs here

        # stop the watch folder observer if it was running
        stop_observer()
