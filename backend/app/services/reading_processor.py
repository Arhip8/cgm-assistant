from collections.abc import Mapping
from copy import deepcopy
from threading import RLock
from typing import Any

from app.api.schemas.reading import Reading
from app.integrations.algorithm import DEMO_RULES, analyze_reading
from app.services.event_store import InMemoryEventStore, event_store
from app.services.notification_service import (
    InMemoryNotificationService,
    notification_service,
)
from app.services.reading_store import reading_store


class ReadingProcessor:
    def __init__(
        self,
        *,
        events: InMemoryEventStore = event_store,
        notifications: InMemoryNotificationService = notification_service,
        rules: Mapping[str, Any] = DEMO_RULES,
    ) -> None:
        self._events = events
        self._notifications = notifications
        self._rules = deepcopy(dict(rules))
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def add_and_analyze(self, reading: Reading) -> bool:
        with self._lock:
            created = reading_store.add(reading)
            if not created:
                return False

            history = [
                item
                for item in reading_store.list(run_id=reading.run_id)
                if item.source_index != reading.source_index
                and item.measured_at < reading.measured_at
            ]
            history = sorted(history, key=lambda item: item.measured_at)[-5:]
            previous_state = self._states.get(reading.run_id)

            try:
                result = analyze_reading(
                    current=reading,
                    history=history,
                    state=previous_state,
                    rules=self._rules,
                )
            except Exception:
                reading_store.remove(reading)
                raise

            if result.status == "processed":
                self._events.add_many(result.events)
                self._notifications.process_events(result.events)
                self._states[reading.run_id] = deepcopy(result.next_state)

            return True

    def state_for(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(run_id)
            return deepcopy(state) if state is not None else None


reading_processor = ReadingProcessor()
