"""Protected administrator routes for stock-selection backtest jobs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.backtesting.tasks import (
    BacktestTaskError,
    BacktestTaskManager,
    get_backtest_task_manager,
)

from .admin import AdminAccess, JsonResponder, RateLimiter


def create_backtesting_router(
    *,
    access: AdminAccess,
    rate_limit: RateLimiter,
    json_response: JsonResponder,
    manager: BacktestTaskManager | None = None,
) -> APIRouter:
    """Create admin-only task creation, status, and options routes."""

    router = APIRouter(include_in_schema=False)
    tasks = manager or get_backtest_task_manager()

    async def require_session(request: Request) -> Response | None:
        limited = rate_limit(request)
        if limited is not None:
            return limited
        if await access.session_valid(request):
            return None
        return JSONResponse(
            {"error": "admin_password_required"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )

    @router.api_route("/api/admin/backtests/options", methods=["GET", "HEAD"])
    async def backtest_options(request: Request) -> Response:
        rejected = await require_session(request)
        if rejected is not None:
            return rejected
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        payload = await run_in_threadpool(tasks.options)
        return json_response(request, payload, cache_control="no-store")

    @router.post("/api/admin/backtests")
    async def start_backtest(request: Request) -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        form, invalid = await access.read_form(request)
        if invalid is not None:
            return invalid
        try:
            payload = await run_in_threadpool(tasks.start, dict(form or {}))
        except BacktestTaskError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return json_response(
            request,
            payload,
            cache_control="no-store",
            status_code=202,
        )

    @router.api_route(
        "/api/admin/backtests/latest/{strategy_id}",
        methods=["GET", "HEAD"],
    )
    async def latest_backtest(request: Request, strategy_id: str) -> Response:
        rejected = await require_session(request)
        if rejected is not None:
            return rejected
        payload = await run_in_threadpool(tasks.latest, strategy_id)
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        return json_response(
            request,
            {"job": payload},
            cache_control="no-store",
        )

    @router.post("/api/admin/backtests/{job_id}/cancel")
    async def cancel_backtest(request: Request, job_id: str) -> Response:
        rejected = await access.require_action(request)
        if rejected is not None:
            return rejected
        try:
            payload = await run_in_threadpool(tasks.cancel, job_id)
        except BacktestTaskError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if payload is None:
            return JSONResponse(
                {"error": "backtest_not_found"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        return json_response(request, payload, cache_control="no-store")

    @router.api_route("/api/admin/backtests/{job_id}", methods=["GET", "HEAD"])
    async def backtest_status(request: Request, job_id: str) -> Response:
        rejected = await require_session(request)
        if rejected is not None:
            return rejected
        payload: dict[str, Any] | None = await run_in_threadpool(tasks.get, job_id)
        if payload is None:
            return JSONResponse(
                {"error": "backtest_not_found"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        return json_response(request, payload, cache_control="no-store")

    return router


__all__ = ["create_backtesting_router"]
