import math
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.readings import router as readings_router
from app.db.database import Database, database
from app.services.reading_processor import ReadingProcessor


def create_app(database_override: Database | None = None) -> FastAPI:
    active_database = database_override or database

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_database.initialize()
        yield

    application = FastAPI(
        title="CGM Assistant API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.reading_processor = ReadingProcessor(
        database=active_database
    )
    application.include_router(health_router)
    application.include_router(readings_router)
    application.include_router(events_router)
    application.include_router(notifications_router)

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

    return application


app = create_app()
