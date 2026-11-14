import math
import os
from pathlib import Path
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.readings import router as readings_router
from app.api.routes.simulation import router as simulation_router
from app.db.database import Database, database
from app.services.reading_processor import ReadingProcessor
from app.services.simulation_controller import SimulationController
from app.services.demo_sessions import COOKIE_NAME, MAX_SESSION_SECONDS, DemoSessions


def create_app(database_override: Database | None = None) -> FastAPI:
    public_demo = os.environ.get("CGM_PUBLIC_DEMO") == "1"
    active_database = database_override or database
    processor = ReadingProcessor(database=active_database)
    simulation_controller = SimulationController(processor=processor)
    sessions = DemoSessions(active_database) if public_demo else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_database.initialize()
        if sessions is None:
            simulation_controller.recover()
        try:
            yield
        finally:
            if sessions is not None:
                sessions.shutdown()
            else:
                simulation_controller.shutdown()

    application = FastAPI(
        title="CGM Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if public_demo else "/docs",
        redoc_url=None if public_demo else "/redoc",
        openapi_url=None if public_demo else "/openapi.json",
    )
    application.state.demo_sessions = sessions

    @application.middleware("http")
    async def demo_session_middleware(request: Request, call_next):
        request.state.reading_processor = processor
        request.state.simulation_controller = simulation_controller
        issued = None
        if sessions is not None and request.url.path.startswith("/api/"):
            # Cross-origin callers cannot set this header without a CORS preflight.
            # No permissive CORS middleware is installed.
            modifying = request.method not in ("GET", "HEAD", "OPTIONS")
            bootstrap = request.url.path == "/api/v1/session"
            if modifying or bootstrap:
                origin = request.headers.get("origin")
                expected = os.environ.get("CGM_PUBLIC_ORIGIN")
                expected = expected.rstrip("/") if expected else f"{request.url.scheme}://{request.url.netloc}"
                if (request.headers.get("x-cgm-demo") != "1"
                    or request.headers.get("sec-fetch-site") == "cross-site"
                    or (origin is not None and origin != expected)):
                    return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
            if request.url.path == "/api/v1/readings" and request.method == "POST":
                return JSONResponse({"detail": "В публичном демо доступен только синтетический симулятор"}, status_code=403)
            try:
                entry, issued = await run_in_threadpool(
                    sessions.resolve, request.cookies.get(COOKIE_NAME), create=bootstrap,
                )
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            request.state.demo_session = entry
            request.state.reading_processor = entry.processor
            request.state.simulation_controller = entry.controller
        response = await call_next(request)
        if sessions is not None:
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            if issued:
                response.set_cookie(COOKIE_NAME, issued, max_age=MAX_SESSION_SECONDS,
                                    httponly=True, secure=True, samesite="strict", path="/")
        return response

    @application.get("/api/v1/session")
    def demo_session_info(request: Request):
        return {
            "public_demo": public_demo,
            "session_key": request.state.reading_processor.owner_id if public_demo else None,
        }

    application.state.reading_processor = processor
    application.state.simulation_controller = simulation_controller
    application.include_router(health_router)
    application.include_router(readings_router)
    application.include_router(events_router)
    application.include_router(notifications_router)
    application.include_router(simulation_router)

    @application.exception_handler(RequestValidationError)
    def handle_request_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = exc.errors()
        for error in errors:
            value = error.get("input")
            if isinstance(value, float) and not math.isfinite(value):
                error["input"] = str(value)

        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": errors}),
        )

    # In the source tree frontend is a sibling of backend; in Docker use an explicit path.
    frontend_dist = Path(os.environ.get("CGM_FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))
    if (frontend_dist / "index.html").is_file():
        application.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @application.get("/", include_in_schema=False)
        def dashboard():
            return FileResponse(frontend_dist / "index.html", headers={"Cache-Control": "no-cache"})

    return application


app = create_app()
