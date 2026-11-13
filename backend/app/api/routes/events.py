from fastapi import APIRouter, Query

from app.api.schemas.analysis import AnalysisEvent
from app.services.event_store import event_store

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("", response_model=list[AnalysisEvent])
def list_events(
    run_id: str | None = Query(default=None, min_length=1),
) -> list[AnalysisEvent]:
    return event_store.list(run_id=run_id)
