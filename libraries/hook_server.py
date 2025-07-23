from fastapi import FastAPI

from libraries.hookschema import HookRequest

tusd_hook_server = FastAPI()


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
