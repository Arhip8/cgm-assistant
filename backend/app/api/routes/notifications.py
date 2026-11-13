from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.schemas.notification import InformationNotification
from app.services.reading_processor import ReadingProcessor
from app.services.notification_service import (
    NotificationNotFoundError,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("", response_model=list[InformationNotification])
def list_notifications(
    request: Request,
    run_id: str | None = Query(default=None, min_length=1),
) -> list[InformationNotification]:
    processor: ReadingProcessor = request.app.state.reading_processor
    return processor.list_notifications(run_id=run_id)


@router.post(
    "/{notification_id}/acknowledge",
    response_model=InformationNotification,
)
def acknowledge_notification(
    notification_id: str,
    request: Request,
) -> InformationNotification:
    try:
        processor: ReadingProcessor = request.app.state.reading_processor
        return processor.acknowledge_notification(notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        ) from exc
