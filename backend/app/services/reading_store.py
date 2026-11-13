from threading import Lock

from app.api.schemas.reading import Reading


class ReadingConflictError(Exception):
    pass


class InMemoryReadingStore:
    def __init__(self) -> None:
        self._readings: dict[str, dict[int, Reading]] = {}
        self._lock = Lock()

    def add(self, reading: Reading) -> bool:
        with self._lock:
            run_readings = self._readings.setdefault(reading.run_id, {})
            existing = run_readings.get(reading.source_index)

            if existing is not None:
                if existing == reading:
                    return False
                raise ReadingConflictError(
                    "A reading with this run_id and source_index already exists"
                )

            run_readings[reading.source_index] = reading
            return True

    def remove(self, reading: Reading) -> bool:
        with self._lock:
            run_readings = self._readings.get(reading.run_id)
            if run_readings is None or run_readings.get(reading.source_index) != reading:
                return False

            del run_readings[reading.source_index]
            if not run_readings:
                del self._readings[reading.run_id]
            return True

    def list(self, run_id: str | None = None) -> list[Reading]:
        with self._lock:
            if run_id is not None:
                readings = self._readings.get(run_id, {})
                return [readings[index] for index in sorted(readings)]

            return [
                reading
                for readings in self._readings.values()
                for _, reading in sorted(readings.items())
            ]


reading_store = InMemoryReadingStore()
