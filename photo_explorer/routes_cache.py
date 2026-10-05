"""Cache usage, clearing, and viewer protection endpoints."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/cache")


class ClearBody(BaseModel):
    root_id: int | None = None


class ProtectBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=31)


@router.get("")
def cache_stats(request: Request) -> dict:
    return request.app.state.cache_policy.stats()


@router.post("/clear")
def clear_cache(request: Request, body: ClearBody | None = None) -> dict:
    return request.app.state.cache_policy.clear(body.root_id if body else None)


@router.post("/protect")
def protect(request: Request, body: ProtectBody) -> dict:
    request.app.state.cache_policy.protect(body.ids)
    return {"ok": True}
