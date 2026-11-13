import math

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.readings import router as readings_router

app = FastAPI(title="CGM Assistant API", version="0.1.0")
app.include_router(health_router)
app.include_router(readings_router)
app.include_router(events_router)
app.include_router(notifications_router)


@app.exception_handler(RequestValidationError)
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
