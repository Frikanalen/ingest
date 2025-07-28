from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from frikanalen_django_api_client import AuthenticatedClient

from app.api.debug.watch_folder.watcher import stop_observer
from app.django_client.service import DjangoApiService
from app.util.ingest_app_state import IngestAppState
from app.util.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler for the FastAPI app. Initializes the authenticated HTTP client
    for the DjangoAPI service and manages its lifecycle cleanly.
    """
    async with AsyncExitStack() as stack:
        client = AuthenticatedClient(
            base_url=str(settings.api.url),
            token=settings.api.key,
            raise_on_unexpected_status=True,
        )

        django_api = DjangoApiService(client)
        await stack.enter_async_context(client)

        app.state.app_state = IngestAppState(django_api=django_api)  # type: ignore[attr-defined]

        # stop the watch folder observer if it was running
        stop_observer()

        yield  # App runs here
