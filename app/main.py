import logging

import uvicorn
from fastapi import FastAPI

from app.api.debug.watch_folder import router as watch_folder_router
from app.api.health.routes import router as internal_router
from app.api.hooks.routes import router as hooks_router
from app.util.lifespan import lifespan
from app.util.settings import settings

app = FastAPI(lifespan=lifespan, debug=True)
logging.basicConfig(level=logging.DEBUG)

app.include_router(internal_router, prefix="/internal")
app.include_router(hooks_router, prefix="/tusdHooks")
app.include_router(watch_folder_router, prefix="/watchFolder")

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
