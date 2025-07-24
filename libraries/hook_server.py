from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from frikanalen_django_api_client import AuthenticatedClient

from libraries.config import config
from libraries.hookschema import HookRequest


@dataclass
class IngestAppState:
    api_client: AuthenticatedClient


def get_client_from_app_state(request: Request) -> AuthenticatedClient:
    return request.app.state.app_state.api_client  # type: ignore[attr-defined]


def build_client():
    return AuthenticatedClient(
        base_url=config.django.base_url, token=config.django.token, raise_on_unexpected_status=True
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler for the FastAPI app. This is where we initialize the HTTP client used by
    the DjangoAPI services. Constructs an authenticated HTTP client based on configuration
    values config.django.base_url and config.django.token.
    """
    client = build_client()
    await client.__aenter__()

    # ✅ Store it on app state
    app.state.app_state = IngestAppState(api_client=client)  # type: ignore[attr-defined]

    yield  # App runs here

    # ✅ Cleanup
    await client.__aexit__(None, None, None)


tusd_hook_server = FastAPI(lifespan=lifespan)


@tusd_hook_server.post("/")
async def receive_hook(hook: HookRequest):
    if hook.type == "post-finish":
        upload = hook.event.upload
        assert upload.storage["Type"] == "filestore", "Only filestore storage is supported"
        print(hook.event.upload.storage["Path"])
    return {"Hello": "World"}


@tusd_hook_server.get("/isAlive")
async def read_health():
    return {"status": "ok"}
