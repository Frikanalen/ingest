import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.api.debug.watch_folder.watcher import stop_observer
from app.django_client.service import DjangoApiService
from app.util.api_get_key import api_get_key
from app.util.ingest_app_state import IngestAppState
from app.util.settings import settings
from frikanalen_django_api_client import AuthenticatedClient

logger = logging.getLogger(__name__)


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
