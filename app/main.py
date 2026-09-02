import logging

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware

from app.api.health.routes import router as internal_router
from app.api.hooks.routes import router as hooks_router
from app.util.lifespan import lifespan
from app.util.settings import IngestAppSettings, get_settings

origins = [
    "http://localhost:3000",
]


def create_app(settings: IngestAppSettings | None = None) -> FastAPI:
    """Build the app that serves tusd's hooks.

    `settings.debug` is what separates serving tusd from a developer's view of
    it: FastAPI's own debug mode, which returns tracebacks to the caller, and
    the /watchFolder endpoints together with the polling observer behind them.
    It defaults off, and so does the whole app when there are no settings to
    read at all -- see `_startup_settings`.
    """
    debug = settings is not None and settings.debug

    app = FastAPI(lifespan=lifespan, debug=debug)

    # Attach the middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,  # or ["*"] to allow all origins (not recommended)
        allow_credentials=True,
        allow_methods=["*"],  # allow all HTTP methods
        allow_headers=["*"],  # allow all headers
    )

    app.include_router(internal_router, prefix="/internal")
    app.include_router(hooks_router, prefix="/tusdHooks")

    if debug:
        # Imported here so that watchdog stays unloaded when debug is off.
        from app.api.debug.watch_folder.routes import router as watch_folder_router

        app.include_router(watch_folder_router, prefix="/watchFolder")

    return app


def _startup_settings() -> IngestAppSettings | None:
    """The settings, or None if the environment does not have any.

    Importing this module must not require a configured environment. The app
    object is built at import time, but configuration belongs to the lifespan,
    so that a misconfigured deployment fails at startup with a readable error
    rather than as an import traceback -- and the tests import the app without
    any configuration at all. Nothing gated on `debug` runs when there is
    nothing to read, which is the safe way round.
    """
    try:
        return get_settings()
    except ValidationError:
        # Not swallowing the problem: the lifespan reads the same settings and
        # raises this on startup, where it is a legible failure.
        return None


_settings = _startup_settings()
logging.basicConfig(level=logging.DEBUG if _settings is not None and _settings.debug else logging.INFO)
app = create_app(_settings)

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
