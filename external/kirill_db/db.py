import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime as SQLDateTime, ForeignKey, Text, JSON,
    UniqueConstraint, CheckConstraint, event, Index, select,
    update,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.types import TypeDecorator

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


def _to_naive_utc(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class UTCDateTime(TypeDecorator):
    impl = SQLDateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return _to_naive_utc(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


DateTime = UTCDateTime


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    simulation_runs = relationship("SimulationRun", back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint = Column(Text, nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User", back_populates="subscriptions")


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    dataset_name = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="idle")
    speed_factor = Column(Float, default=1.0)
    source_position = Column(Integer, default=0)
    started_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="simulation_runs")
    readings = relationship("GlucoseReading", back_populates="simulation_run", cascade="all, delete-orphan")
    events = relationship("GlucoseEvent", back_populates="simulation_run", cascade="all, delete-orphan")
    analysis_state = relationship(
        "AnalysisState", back_populates="simulation_run", uselist=False,
        cascade="all, delete-orphan", single_parent=True,
    )


class GlucoseReading(Base):
    __tablename__ = "glucose_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    source_index = Column(Integer, nullable=False)
    measured_at = Column(DateTime, nullable=False, index=True)
    value_mmol_l = Column(Float, nullable=False)
    received_at = Column(DateTime, default=utcnow, nullable=False)

    simulation_run = relationship("SimulationRun", back_populates="readings")

    __table_args__ = (
        UniqueConstraint("run_id", "source_index", name="_run_source_uc"),
    )


class GlucoseEvent(Base):
    __tablename__ = "glucose_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(Text, nullable=False)
    event_uid = Column(String(36), nullable=False)
    phase = Column(Text, nullable=False)
    severity = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    measured_at = Column(DateTime, nullable=True)
    glucose_mmol_l = Column(Float, nullable=True)
    details = Column(MutableDict.as_mutable(JSON), nullable=True)
    origin = Column(Text, nullable=True)
    priority_hint = Column(Integer, nullable=True)

    simulation_run = relationship("SimulationRun", back_populates="events")
    notifications = relationship("Notification", back_populates="glucose_event", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("run_id", "event_uid", "phase", name="_run_event_phase_uc"),
        Index("ix_events_run_uid", "run_id", "event_uid"),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("glucose_events.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    acknowledged_at = Column(DateTime, nullable=True)
    push_status = Column(Text, default="pending")
    attempt_count = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=True)

    glucose_event = relationship("GlucoseEvent", back_populates="notifications")


class AnalysisState(Base):
    __tablename__ = "analysis_state"

    run_id = Column(String(36), ForeignKey("simulation_runs.id", ondelete="CASCADE"), primary_key=True)
    last_source_index = Column(Integer, nullable=True)
    last_measured_at = Column(DateTime, nullable=True)
    last_value_mmol_l = Column(Float, nullable=True)
    cur_band = Column(MutableDict.as_mutable(JSON), nullable=True)
    cur_trend = Column(MutableDict.as_mutable(JSON), nullable=True)
    rise_count = Column(Integer, nullable=False, default=0, server_default="0")
    fall_count = Column(Integer, nullable=False, default=0, server_default="0")
    schema_ver = Column(Integer, nullable=False, default=1, server_default="1")
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    revision = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        CheckConstraint("rise_count >= 0", name="ck_a_s_rise_non_negative"),
        CheckConstraint("fall_count >= 0", name="ck_a_s_fall_non_negative"),
    )

    simulation_run = relationship("SimulationRun", back_populates="analysis_state")


def _configure_sqlite(eng):
    if eng.dialect.name != "sqlite":
        return eng

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


DB_URL = "sqlite:///diabetes_helper.db"
engine = _configure_sqlite(create_engine(DB_URL, echo=False))
Session = sessionmaker(bind=engine)


class StaleStateError(Exception):
    pass


def _is_duplicate_reading(exc):
    orig = getattr(exc, "orig", exc)
    if getattr(orig, "pgcode", None) == "23505" or getattr(orig, "sqlstate", None) == "23505":
        return getattr(getattr(orig, "diag", None), "constraint_name", None) == "_run_source_uc"
    msg = str(orig)
    return "UNIQUE constraint failed: glucose_readings.run_id, glucose_readings.source_index" in msg or (
        "_run_source_uc" in msg and "duplicate key value" in msg
    )


def _write_state(session, new_state, expected_revision):
    existing = session.execute(
        select(AnalysisState).where(AnalysisState.run_id == new_state.run_id)
    ).scalar_one_or_none()
    if existing is None:
        if expected_revision not in (None, 0):
            raise StaleStateError(f"analysis_state for run {new_state.run_id} is missing")
        new_state.revision = 1
        session.add(new_state)
        return

    if expected_revision is None:
        raise StaleStateError(
            f"analysis_state for run {new_state.run_id} exists; pass expected_revision"
        )

    values = {}
    for col in AnalysisState.__table__.columns:
        if col.name in ("run_id", "revision", "updated_at"):
            continue
        value = getattr(new_state, col.name)
        if value is None and not col.nullable:
            value = getattr(existing, col.name)
        values[col.name] = value
    values["revision"] = expected_revision + 1
    values["updated_at"] = utcnow()

    result = session.execute(
        update(AnalysisState)
        .where(
            AnalysisState.run_id == new_state.run_id,
            AnalysisState.revision == expected_revision,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise StaleStateError(
            f"analysis_state for run {new_state.run_id} changed; "
            f"expected revision {expected_revision}"
        )
    new_state.revision = expected_revision + 1


def save_tick(session, *, run_id, reading, events=(), new_state=None,
              run=None, expected_revision=None):
    ctx = session.begin_nested() if session.in_transaction() else session.begin()
    try:
        with ctx:
            session.add(reading)
            session.flush()
            for ev in events:
                session.add(ev)
                for n in getattr(ev, "_pending_notifications", ()):
                    payload = {k: v for k, v in n.items() if k not in ("run_id", "glucose_event")}
                    session.add(Notification(glucose_event=ev, run_id=run_id, **payload))
            if new_state is not None:
                _write_state(session, new_state, expected_revision)
            if run is not None:
                run.source_position += 1
            session.flush()
        return True
    except IntegrityError as exc:
        if _is_duplicate_reading(exc):
            return False
        raise


def init_database():
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    init_database()
