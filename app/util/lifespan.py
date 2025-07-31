import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from frikanalen_django_api_client import AuthenticatedClient

from app.api.debug.watch_folder.watcher import stop_watch_folder
from app.django_client.service import DjangoApiService
from app.util.api_get_key import api_get_key
from app.util.ingest_app_state import IngestAppState
from app.util.settings import DjangoApiSettingsPwdAuth, DjangoApiSettingsTokenAuth, get_settings

logger = logging.getLogger(__name__)


def get_token(api_settings: DjangoApiSettingsTokenAuth | DjangoApiSettingsPwdAuth) -> str | None:
    if isinstance(api_settings, DjangoApiSettingsPwdAuth):
        return api_get_key(
            api_settings.url,
            api_settings.username,
            api_settings.password.get_secret_value(),
        )
    elif isinstance(api_settings, DjangoApiSettingsTokenAuth):
        return api_settings.token.get_secret_value()
    else:
        raise ValueError("Unsupported API authentication method")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        settings = get_settings()
        token = get_token(settings.api)

        client = AuthenticatedClient(
            base_url=str(settings.api.url),
            token=token,
            prefix="Token",
            raise_on_unexpected_status=True,
            follow_redirects=True,
        )

        django_api = DjangoApiService(client)
        await stack.enter_async_context(client)

        app.state.app_state = IngestAppState(django_api=django_api)  # type: ignore[attr-defined]

        # fixme: should only happen in debug mode
        from app.api.debug.watch_folder.watcher import start_watchfolder

        logger.info("Starting directory watcher for %s", settings.debug.watchdir)
        start_watchfolder(settings.debug.watchdir)

        yield  # App runs here

        # stop the watch folder observer if it was running
        stop_watch_folder()
