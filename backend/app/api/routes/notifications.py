from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas.notification import InformationNotification
from app.services.notification_service import (
    NotificationNotFoundError,
    notification_service,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("", response_model=list[InformationNotification])
def list_notifications(
    run_id: str | None = Query(default=None, min_length=1),
) -> list[InformationNotification]:
    return notification_service.list(run_id=run_id)


@router.post(
    "/{notification_id}/acknowledge",
    response_model=InformationNotification,
)
def acknowledge_notification(notification_id: str) -> InformationNotification:
    try:
        return notification_service.acknowledge(notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        ) from exc
