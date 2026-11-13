from threading import Lock

from app.api.schemas.analysis import AnalysisEvent


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[AnalysisEvent] = []
        self._lock = Lock()

    def add_many(self, events: list[AnalysisEvent]) -> None:
        with self._lock:
            self._events.extend(events)

    def list(self, run_id: str | None = None) -> list[AnalysisEvent]:
        with self._lock:
            selected = (
                event for event in self._events
                if run_id is None or event.run_id == run_id
            )
            return [event.model_copy(deep=True) for event in selected]


event_store = InMemoryEventStore()
