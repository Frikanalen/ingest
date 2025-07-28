from fastapi import APIRouter

router = APIRouter()


@router.get("/isAlive")
async def read_health():
    return {"status": "ok"}
