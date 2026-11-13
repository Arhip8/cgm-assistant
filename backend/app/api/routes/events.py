from fastapi import APIRouter, Query, Request

from app.api.schemas.analysis import AnalysisEvent
from app.services.reading_processor import ReadingProcessor

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("", response_model=list[AnalysisEvent])
def list_events(
    request: Request,
    run_id: str | None = Query(default=None, min_length=1),
) -> list[AnalysisEvent]:
    processor: ReadingProcessor = request.app.state.reading_processor
    return processor.list_events(run_id=run_id)
