import logging

from fastapi import APIRouter
from starlette.requests import Request

from app.api.hooks.post_finish import post_finish
from app.api.hooks.pre_create import pre_create
from app.api.hooks.schema import HookRequest
from app.util.app_state import get_app_state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/")
async def receive_hook(hook_request: HookRequest, request: Request):
    logger.info("Received hook: %s", hook_request.type)
    if hook_request.type == "pre-create":
        return await pre_create(hook_request)

    if hook_request.type == "post-finish":
        return await post_finish(hook_request, get_app_state(request).django_api)

    return {}
