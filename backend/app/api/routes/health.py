from fastapi import APIRouter, Response, status

from app.infrastructure.health import check_dependencies

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "promotiva-api"}


@router.get("/ready")
async def readiness(response: Response) -> dict[str, object]:
    services = await check_dependencies()
    ready = all(result == "ok" for result in services.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "services": services}
