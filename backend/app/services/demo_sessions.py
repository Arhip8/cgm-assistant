"""Bounded anonymous demo sessions. One process / one Uvicorn worker only."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from threading import RLock
from time import monotonic
import re

from fastapi import HTTPException
from sqlalchemy import select

from app.db.database import Database
from app.services.reading_processor import ReadingProcessor
from app.services.simulation_controller import PROJECT_ROOT, SimulationController
from external.kirill_db.db import User, SimulationRun

COOKIE_NAME = "__Host-cgm_demo"
MAX_SESSIONS = 64
MAX_ACTIVE = 8  # Paused workers also consume a slot.
MAX_RUNS = 5
SESSION_IDLE_SECONDS = 15 * 60
MAX_SESSION_SECONDS = 60 * 60
MIN_START_INTERVAL = 5


@dataclass
class DemoSession:
    processor: ReadingProcessor
    controller: SimulationController
    touched: float
    created: float
    last_start: float = float("-inf")


class DemoSessions:
    def __init__(self, database: Database):
        self.database = database
        self.lock = RLock()
        self.sessions: dict[str, DemoSession] = {}

    @staticmethod
    def owner_id(token: str) -> str:
        # Never persist the bearer secret itself.
        return sha256(token.encode()).hexdigest()[:32]

    def _cleanup(self, now: float) -> None:
        for owner, entry in list(self.sessions.items()):
            if now - entry.touched > SESSION_IDLE_SECONDS or now - entry.created > MAX_SESSION_SECONDS:
                entry.controller.shutdown()
                with self.database.session() as db, db.begin():
                    user = db.get(User, owner)
                    if user is not None:
                        db.delete(user)
                del self.sessions[owner]

    def resolve(self, token: str | None, *, create: bool = False) -> tuple[DemoSession, str | None]:
        with self.lock:
            now = monotonic()
            self._cleanup(now)
            valid = token is not None and re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
            owner = self.owner_id(token) if valid else None
            if owner in self.sessions:
                entry = self.sessions[owner]
                entry.touched = now
                return entry, None
            # A fresh process restores only a token with a persisted matching user.
            with self.database.session() as db:
                exists = owner is not None and db.get(User, owner) is not None
            if not exists and not create:
                raise HTTPException(401, "Демонстрационный сеанс истёк")
            if len(self.sessions) >= MAX_SESSIONS:
                raise HTTPException(503, "Демо занято. Повторите попытку позже", headers={"Retry-After": "30"})
            issued = None
            if not exists:
                with self.database.session() as db, db.begin():
                    users = list(db.scalars(select(User).where(User.display_name == "Anonymous synthetic demo")))
                    for user in users:
                        if user.id not in self.sessions and user.created_at < datetime.now(timezone.utc) - timedelta(seconds=MAX_SESSION_SECONDS):
                            db.delete(user)
                    db.flush()
                    remaining = list(db.scalars(select(User.id).where(User.display_name == "Anonymous synthetic demo")))
                    if len(remaining) >= MAX_SESSIONS:
                        raise HTTPException(503, "Демо занято. Повторите попытку позже")
                issued = token_urlsafe(32)
                owner = self.owner_id(issued)
                with self.database.session() as db, db.begin():
                    db.add(User(id=owner, display_name="Anonymous synthetic demo"))
            processor = ReadingProcessor(database=self.database, owner_id=owner)
            controller = SimulationController(
                processor=processor, csv_path=PROJECT_ROOT / "demo" / "synthetic.csv",
                timezone_name="UTC", synthetic=True,
            )
            controller.recover()
            entry = DemoSession(processor, controller, now, now)
            self.sessions[owner] = entry
            return entry, issued

    def control(self, entry: DemoSession, action: str, speed: float | None = None):
        # Admission and starting the worker are atomic across visitors.
        with self.lock:
            if self.sessions.get(entry.processor.owner_id) is not entry:
                raise HTTPException(401, "Демонстрационный сеанс истёк")
            if action in ("start", "resume"):
                active = sum(
                    item.controller.status()["status"] in ("running", "paused")
                    for item in self.sessions.values()
                )
                already_active = entry.controller.status()["status"] in ("running", "paused")
                if not already_active and active >= MAX_ACTIVE:
                    raise HTTPException(429, "Все места симуляции заняты. Попробуйте позже", headers={"Retry-After": "10"})
            if action == "start":
                if monotonic() - entry.last_start < MIN_START_INTERVAL:
                    raise HTTPException(429, "Подождите 5 секунд перед новым запуском")
                result = entry.controller.start(speed)
                entry.last_start = monotonic()
                # Bound stored history per visitor, including the new run.
                with self.database.session() as db, db.begin():
                    old_runs = list(db.scalars(
                        select(SimulationRun).where(SimulationRun.user_id == entry.processor.owner_id)
                        .order_by(SimulationRun.started_at.desc(), SimulationRun.id.desc())
                    ))
                    for run in old_runs[MAX_RUNS:]:
                        db.delete(run)
                return result
            return getattr(entry.controller, action)()

    def shutdown(self):
        with self.lock:
            for entry in self.sessions.values():
                entry.controller.shutdown()
