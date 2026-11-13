from datetime import datetime, timezone
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from app.api.schemas.analysis import AnalysisEvent, EventPhase
from app.api.schemas.notification import (
    InformationNotification,
    NotificationStatus,
)

_NOTIFICATION_RULES: dict[str, tuple[str, str]] = {
    "demo_above_range": (
        "Наблюдение алгоритма: демонстрационный диапазон",
        "Алгоритм зарегистрировал демонстрационное событие "
        "«выше демонстрационной границы». Значение измерения: {value} ммоль/л.",
    ),
    "demo_below_range": (
        "Наблюдение алгоритма: демонстрационный диапазон",
        "Алгоритм зарегистрировал демонстрационное событие "
        "«ниже демонстрационной границы». Значение измерения: {value} ммоль/л.",
    ),
    "demo_rapid_rise": (
        "Наблюдение алгоритма: изменение показания",
        "Алгоритм зарегистрировал демонстрационное событие "
        "«быстрый рост». Значение измерения: {value} ммоль/л.",
    ),
    "demo_rapid_fall": (
        "Наблюдение алгоритма: изменение показания",
        "Алгоритм зарегистрировал демонстрационное событие "
        "«быстрое снижение». Значение измерения: {value} ммоль/л.",
    ),
}


class NotificationNotFoundError(Exception):
    pass


class InMemoryNotificationService:
    def __init__(self) -> None:
        self._notifications: list[InformationNotification] = []
        self._by_event_id: dict[str, InformationNotification] = {}
        self._lock = Lock()

    def process_events(
        self,
        events: list[AnalysisEvent],
    ) -> list[InformationNotification]:
        created: list[InformationNotification] = []
        with self._lock:
            for event in events:
                rule = _NOTIFICATION_RULES.get(event.event_type)
                if event.phase != EventPhase.STARTED or rule is None:
                    continue
                if event.event_id in self._by_event_id:
                    continue

                title, text_template = rule
                notification = InformationNotification(
                    id=str(uuid5(NAMESPACE_URL, f"cgm-assistant:event:{event.event_id}")),
                    run_id=event.run_id,
                    event_id=event.event_id,
                    title=title,
                    text=text_template.format(value=f"{event.glucose_mmol_l:g}"),
                    created_at=datetime.now(timezone.utc),
                    status=NotificationStatus.UNREAD,
                    acknowledged_at=None,
                )
                self._notifications.append(notification)
                self._by_event_id[event.event_id] = notification
                created.append(notification.model_copy(deep=True))

        return created

    def list(self, run_id: str | None = None) -> list[InformationNotification]:
        with self._lock:
            return [
                notification.model_copy(deep=True)
                for notification in self._notifications
                if run_id is None or notification.run_id == run_id
            ]

    def acknowledge(self, notification_id: str) -> InformationNotification:
        with self._lock:
            for notification in self._notifications:
                if notification.id == notification_id:
                    if notification.status == NotificationStatus.UNREAD:
                        notification.status = NotificationStatus.READ
                        notification.acknowledged_at = datetime.now(timezone.utc)
                    return notification.model_copy(deep=True)

        raise NotificationNotFoundError(notification_id)


notification_service = InMemoryNotificationService()
