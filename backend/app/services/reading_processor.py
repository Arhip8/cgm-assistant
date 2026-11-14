from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.api.schemas.analysis import AnalysisEvent, EventPhase
from app.api.schemas.notification import (
    InformationNotification,
    NotificationStatus,
)
from app.api.schemas.reading import Reading
from app.api.schemas.simulation import DEFAULT_SPEED_MULTIPLIER
from app.db.database import Database, database
from app.integrations.algorithm import DEMO_RULES, analyze_reading
from app.services.event_store import InMemoryEventStore, event_store
from app.services.notification_service import (
    InMemoryNotificationService,
    NotificationNotFoundError,
    notification_content,
    notification_service,
)
from app.services.reading_store import (
    InMemoryReadingStore,
    ReadingConflictError,
    reading_store,
)


def _database_models() -> Any:
    from external.kirill_db import db

    return db


def _internal_run_id(run_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"cgm-assistant:simulation-run:{run_id}"))


def _demo_user_id() -> str:
    return str(uuid5(NAMESPACE_URL, "cgm-assistant:demo-user"))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _event_model(run_id: str, event: AnalysisEvent) -> Any:
    models = _database_models()
    phase_status = {
        EventPhase.STARTED: "active",
        EventPhase.ENDED: "ended",
        EventPhase.INTERRUPTED: "interrupted",
        EventPhase.OBSERVED: "observed",
    }
    return models.GlucoseEvent(
        run_id=run_id,
        event_type=event.event_type,
        event_uid=event.event_id,
        phase=event.phase.value,
        severity="informational",
        status=phase_status[event.phase],
        started_at=_as_datetime(event.started_at),
        source_started_at=event.started_at,
        ended_at=_as_datetime(event.ended_at),
        source_ended_at=event.ended_at,
        measured_at=_as_datetime(event.measured_at),
        source_measured_at=event.measured_at,
        glucose_mmol_l=event.glucose_mmol_l,
        details=deepcopy(event.details),
        origin=event.origin,
        priority_hint=event.priority_hint,
    )


class ReadingProcessor:
    def __init__(
        self,
        *,
        database: Database | None = None,
        events: InMemoryEventStore = event_store,
        notifications: InMemoryNotificationService = notification_service,
        readings: InMemoryReadingStore = reading_store,
        rules: Mapping[str, Any] = DEMO_RULES,
    ) -> None:
        self._database = database
        self._events = events
        self._notifications = notifications
        self._readings = readings
        self._rules = deepcopy(dict(rules))
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def add_and_analyze(self, reading: Reading) -> bool:
        with self._lock:
            if self._database is None:
                return self._add_in_memory(reading)
            return self._add_persistently(reading)

    def _add_in_memory(self, reading: Reading) -> bool:
        created = self._readings.add(reading)
        if not created:
            return False

        history = [
            item
            for item in self._readings.list(run_id=reading.run_id)
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
            self._readings.remove(reading)
            raise

        if result.status == "processed":
            self._events.add_many(result.events)
            self._notifications.process_events(result.events)
            self._states[reading.run_id] = deepcopy(result.next_state)
        return True

    def _add_persistently(self, reading: Reading) -> bool:
        assert self._database is not None
        models = _database_models()
        internal_run_id = _internal_run_id(reading.run_id)

        with self._database.session() as session, session.begin():
            simulation_run = session.get(models.SimulationRun, internal_run_id)
            if simulation_run is None:
                demo_user_id = _demo_user_id()
                if session.get(models.User, demo_user_id) is None:
                    session.add(
                        models.User(
                            id=demo_user_id,
                            display_name="CGM Assistant Demo",
                        )
                    )
                simulation_run = models.SimulationRun(
                    id=internal_run_id,
                    user_id=demo_user_id,
                    dataset_name=reading.run_id,
                    status="running",
                )
                session.add(simulation_run)
                session.flush()

            existing = session.execute(
                select(models.GlucoseReading).where(
                    models.GlucoseReading.run_id == internal_run_id,
                    models.GlucoseReading.source_index == reading.source_index,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if self._matches(existing, reading):
                    return False
                raise ReadingConflictError(
                    "A reading with this run_id and source_index already exists"
                )

            persisted_history = session.execute(
                select(models.GlucoseReading)
                .where(
                    models.GlucoseReading.run_id == internal_run_id,
                    models.GlucoseReading.measured_at < reading.measured_at,
                )
                .order_by(models.GlucoseReading.measured_at.desc())
                .limit(5)
            ).scalars()
            history = [
                Reading(
                    run_id=reading.run_id,
                    source_index=item.source_index,
                    measured_at=(
                        item.source_measured_at or _iso(item.measured_at)
                    ),
                    glucose_mmol_l=item.value_mmol_l,
                )
                for item in reversed(list(persisted_history))
            ]

            saved_state = session.get(models.AnalysisState, internal_run_id)
            state = self._restore_state(reading.run_id, saved_state)
            result = analyze_reading(
                current=reading,
                history=history,
                state=state,
                rules=self._rules,
            )

            stored_reading = models.GlucoseReading(
                run_id=internal_run_id,
                source_index=reading.source_index,
                measured_at=reading.measured_at,
                source_measured_at=reading.measured_at.isoformat(),
                value_mmol_l=reading.glucose_mmol_l,
            )
            event_models: list[Any] = []
            if result.status == "processed":
                for event in result.events:
                    stored_event = _event_model(internal_run_id, event)
                    content = notification_content(event)
                    if content is not None:
                        title, body = content
                        stored_event._pending_notifications = [
                            {"title": title, "body": body}
                        ]
                    event_models.append(stored_event)

            new_state = None
            if result.status == "processed":
                next_state = result.next_state
                new_state = models.AnalysisState(
                    run_id=internal_run_id,
                    last_source_index=reading.source_index,
                    last_measured_at=_as_datetime(
                        next_state.get("last_measured_at")
                    ),
                    last_value_mmol_l=next_state.get("last_value_mmol_l"),
                    cur_band=deepcopy(next_state.get("band")),
                    cur_trend=deepcopy(next_state.get("trend")),
                    rise_count=next_state.get("rise_count", 0),
                    fall_count=next_state.get("fall_count", 0),
                    schema_ver=1,
                )

            saved = _database_models().save_tick(
                session,
                run_id=internal_run_id,
                reading=stored_reading,
                events=event_models,
                new_state=new_state,
                run=simulation_run,
                expected_revision=saved_state.revision if saved_state else 0,
            )
            if not saved:
                duplicate = session.execute(
                    select(models.GlucoseReading).where(
                        models.GlucoseReading.run_id == internal_run_id,
                        models.GlucoseReading.source_index == reading.source_index,
                    )
                ).scalar_one()
                if self._matches(duplicate, reading):
                    return False
                raise ReadingConflictError(
                    "A reading with this run_id and source_index already exists"
                )
        return True

    @staticmethod
    def _matches(stored: Any, reading: Reading) -> bool:
        stored_time = _as_datetime(stored.measured_at)
        submitted_time = _as_datetime(reading.measured_at)
        return (
            stored.source_index == reading.source_index
            and stored_time == submitted_time
            and stored.value_mmol_l == reading.glucose_mmol_l
        )

    @staticmethod
    def _restore_state(
        public_run_id: str,
        saved_state: Any | None,
    ) -> dict[str, Any] | None:
        if saved_state is None:
            return None
        return {
            "run_id": public_run_id,
            "last_measured_at": _iso(saved_state.last_measured_at),
            "last_value_mmol_l": saved_state.last_value_mmol_l,
            "band": deepcopy(saved_state.cur_band),
            "trend": deepcopy(saved_state.cur_trend),
            "rise_count": saved_state.rise_count,
            "fall_count": saved_state.fall_count,
        }

    def list_readings(self, run_id: str | None = None) -> list[Reading]:
        if self._database is None:
            return self._readings.list(run_id=run_id)
        models = _database_models()
        statement = (
            select(models.GlucoseReading, models.SimulationRun.dataset_name)
            .join(
                models.SimulationRun,
                models.SimulationRun.id == models.GlucoseReading.run_id,
            )
        )
        if run_id is not None:
            statement = statement.where(models.SimulationRun.dataset_name == run_id)
        statement = statement.order_by(
            models.SimulationRun.dataset_name,
            models.GlucoseReading.source_index,
        )
        with self._database.session() as session:
            return [
                Reading(
                    run_id=dataset_name,
                    source_index=item.source_index,
                    measured_at=(
                        item.source_measured_at or _iso(item.measured_at)
                    ),
                    glucose_mmol_l=item.value_mmol_l,
                )
                for item, dataset_name in session.execute(statement)
            ]

    def list_events(self, run_id: str | None = None) -> list[AnalysisEvent]:
        if self._database is None:
            return self._events.list(run_id=run_id)
        models = _database_models()
        statement = (
            select(models.GlucoseEvent, models.SimulationRun.dataset_name)
            .join(
                models.SimulationRun,
                models.SimulationRun.id == models.GlucoseEvent.run_id,
            )
        )
        if run_id is not None:
            statement = statement.where(models.SimulationRun.dataset_name == run_id)
        statement = statement.order_by(
            models.SimulationRun.dataset_name,
            models.GlucoseEvent.measured_at,
            models.GlucoseEvent.id,
        )
        with self._database.session() as session:
            return [
                AnalysisEvent(
                    event_id=item.event_uid,
                    run_id=dataset_name,
                    event_type=item.event_type,
                    phase=item.phase,
                    origin=item.origin or "",
                    priority_hint=item.priority_hint or "",
                    started_at=item.source_started_at or _iso(item.started_at) or "",
                    ended_at=item.source_ended_at or _iso(item.ended_at),
                    measured_at=item.source_measured_at or _iso(item.measured_at) or "",
                    glucose_mmol_l=item.glucose_mmol_l,
                    details=item.details,
                )
                for item, dataset_name in session.execute(statement)
            ]

    def list_notifications(
        self,
        run_id: str | None = None,
    ) -> list[InformationNotification]:
        if self._database is None:
            return self._notifications.list(run_id=run_id)
        models = _database_models()
        statement = (
            select(models.Notification, models.SimulationRun.dataset_name,
                   models.GlucoseEvent.event_uid)
            .join(
                models.SimulationRun,
                models.SimulationRun.id == models.Notification.run_id,
            )
            .join(
                models.GlucoseEvent,
                models.GlucoseEvent.id == models.Notification.event_id,
            )
        )
        if run_id is not None:
            statement = statement.where(models.SimulationRun.dataset_name == run_id)
        statement = statement.order_by(
            models.SimulationRun.dataset_name,
            models.Notification.created_at,
            models.Notification.id,
        )
        with self._database.session() as session:
            return [
                self._notification_schema(item, dataset_name, event_uid)
                for item, dataset_name, event_uid in session.execute(statement)
            ]

    def acknowledge_notification(self, notification_id: str) -> InformationNotification:
        if self._database is None:
            return self._notifications.acknowledge(notification_id)
        models = _database_models()
        try:
            db_id = int(notification_id)
        except ValueError as exc:
            raise NotificationNotFoundError(notification_id) from exc
        with self._database.session() as session, session.begin():
            item = session.get(models.Notification, db_id)
            if item is None:
                raise NotificationNotFoundError(notification_id)
            if item.acknowledged_at is None:
                item.acknowledged_at = datetime.now(timezone.utc)
                item.push_status = "acknowledged"
            run = session.get(models.SimulationRun, item.run_id)
            event = session.get(models.GlucoseEvent, item.event_id)
            if run is None or event is None:
                raise NotificationNotFoundError(notification_id)
            session.flush()
            return self._notification_schema(item, run.dataset_name, event.event_uid)

    @staticmethod
    def _notification_schema(
        item: Any,
        public_run_id: str,
        event_uid: str,
    ) -> InformationNotification:
        acknowledged = item.acknowledged_at is not None
        return InformationNotification(
            id=str(item.id),
            run_id=public_run_id,
            event_id=event_uid,
            title=item.title,
            text=item.body,
            created_at=item.created_at,
            status=NotificationStatus.READ if acknowledged else NotificationStatus.UNREAD,
            acknowledged_at=item.acknowledged_at,
        )

    def state_for(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self._database is None:
                state = self._states.get(run_id)
                return deepcopy(state) if state is not None else None
            models = _database_models()
            with self._database.session() as session:
                stored = session.get(models.AnalysisState, _internal_run_id(run_id))
                return self._restore_state(run_id, stored)

    def prepare_simulation_run(
        self,
        run_id: str,
        speed_multiplier: float,
    ) -> None:
        if self._database is None:
            raise RuntimeError("Server simulations require persistent storage")
        models = _database_models()
        with self._lock:
            with self._database.session() as session, session.begin():
                simulation_run = session.get(
                    models.SimulationRun,
                    _internal_run_id(run_id),
                )
                if simulation_run is not None:
                    raise RuntimeError(f"Simulation run already exists: {run_id}")
                demo_user_id = _demo_user_id()
                if session.get(models.User, demo_user_id) is None:
                    session.add(
                        models.User(
                            id=demo_user_id,
                            display_name="CGM Assistant Demo",
                        )
                    )
                session.add(
                    models.SimulationRun(
                        id=_internal_run_id(run_id),
                        user_id=demo_user_id,
                        dataset_name=run_id,
                        status="running",
                        speed_factor=speed_multiplier,
                        source_position=0,
                    )
                )

    def set_simulation_status(
        self,
        run_id: str,
        status: str,
        speed_multiplier: float | None = None,
    ) -> None:
        if self._database is None:
            raise RuntimeError("Server simulations require persistent storage")
        models = _database_models()
        with self._lock:
            with self._database.session() as session, session.begin():
                simulation_run = session.get(
                    models.SimulationRun,
                    _internal_run_id(run_id),
                )
                if simulation_run is None:
                    raise RuntimeError(f"Simulation run not found: {run_id}")
                simulation_run.status = status
                if speed_multiplier is not None:
                    simulation_run.speed_factor = speed_multiplier

    def simulation_progress(self, run_id: str) -> int:
        if self._database is None:
            raise RuntimeError("Server simulations require persistent storage")
        models = _database_models()
        with self._lock:
            with self._database.session() as session:
                simulation_run = session.get(
                    models.SimulationRun,
                    _internal_run_id(run_id),
                )
                return (
                    simulation_run.source_position
                    if simulation_run is not None
                    else 0
                )

    def latest_server_simulation(
        self,
    ) -> tuple[str, str, int, float] | None:
        if self._database is None:
            raise RuntimeError("Server simulations require persistent storage")
        models = _database_models()
        with self._lock:
            with self._database.session() as session:
                simulation_run = session.execute(
                    select(models.SimulationRun)
                    .where(models.SimulationRun.dataset_name.like("server-sim-%"))
                    .order_by(
                        models.SimulationRun.started_at.desc(),
                        models.SimulationRun.id.desc(),
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if simulation_run is None:
                    return None
                return (
                    simulation_run.dataset_name,
                    simulation_run.status,
                    simulation_run.source_position,
                    simulation_run.speed_factor or DEFAULT_SPEED_MULTIPLIER,
                )


reading_processor = ReadingProcessor(database=database)
