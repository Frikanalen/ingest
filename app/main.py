import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from frikanalen_django_api_client import AuthenticatedClient
from pydantic import BaseModel, Field, ValidationError
from werkzeug.utils import secure_filename

from app.archive import Archive
from app.converter import Converter
from app.django_api.build_client import build_client
from app.django_api.service import DjangoApiService
from app.ingest import Ingester
from app.tus_hook.hook_schema import HookRequest
from runner import Runner


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


app = FastAPI(lifespan=lifespan)


@dataclass
class IngestAppState:
    api_client: AuthenticatedClient


@app.post("/")
async def receive_hook(hook: HookRequest):
    if hook.type == "pre-create":
        try:
            UploadMetaData(**hook.event.upload.meta_data.model_dump())
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors()) from e
        except AttributeError as e:
            raise HTTPException(status_code=422, detail="Missing required fields") from e

    if hook.type == "post-finish":
        try:
            upload_meta = UploadMetaData(**hook.event.upload.meta_data.model_dump())
            assert hook.event.upload.storage["Type"] == "filestore", "Only filestore storage is supported"
            print(f"outpath: {hook.event.upload.storage['Path']}")

        except ValidationError as e:
            logging.error(e.errors)
            raise HTTPException(status_code=422, detail=e.errors()) from e
        except AttributeError as e:
            logging.error(e)
            raise HTTPException(status_code=422, detail="Missing required fields") from e

        django_api = AsyncMock(spec=DjangoApiService)
        ingest = Ingester(
            video_id=upload_meta.video_id,
            django_api=django_api,
            converter_service=Converter(django_api, Runner()),
            archive=Archive(),
        )
        out_path = Path(hook.event.upload.storage["Path"])
        if not out_path.exists():
            raise HTTPException(status_code=500, detail=f"File not found: {out_path}")

        # rename out_path to original file name
        out_path.rename(out_path.parent / secure_filename(upload_meta.orig_file_name))
        await ingest.ingest(out_path)
    return {}


class UploadMetaData(BaseModel):
    video_id: str = Field(..., alias="VideoID")
    orig_file_name: str = Field(..., alias="OrigFileName")
    upload_token: str = Field(..., alias="UploadToken")


@app.get("/isAlive")
async def read_health():
    return {"status": "ok"}
