from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.api.schemas.reading import Reading
from app.services.reading_processor import ReadingProcessor
from app.services.reading_store import ReadingConflictError

router = APIRouter(prefix="/api/v1/readings", tags=["readings"])


@router.post("", response_model=Reading, status_code=status.HTTP_201_CREATED)
def create_reading(
    reading: Reading,
    request: Request,
    response: Response,
) -> Reading:
    try:
        processor: ReadingProcessor = request.app.state.reading_processor
        created = processor.add_and_analyze(reading)
    except ReadingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if not created:
        response.status_code = status.HTTP_200_OK
    return reading


@router.get("", response_model=list[Reading])
def list_readings(
    request: Request,
    run_id: str | None = Query(default=None, min_length=1),
) -> list[Reading]:
    processor: ReadingProcessor = request.app.state.reading_processor
    return processor.list_readings(run_id=run_id)
