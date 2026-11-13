from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class EventPhase(str, Enum):
    STARTED = "started"
    ENDED = "ended"
    INTERRUPTED = "interrupted"
    OBSERVED = "observed"


class AnalysisEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    run_id: str
    event_type: str
    phase: EventPhase
    origin: str
    priority_hint: str
    started_at: str
    ended_at: str | None
    measured_at: str
    glucose_mmol_l: float
    details: dict[str, Any]


class AnalysisMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta_mmol_l: float | None
    rate_mmol_l_min: float | None
    minutes_since_previous: float | None


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["processed", "duplicate", "out_of_order"]
    run_id: str
    source_index: int
    measured_at: str
    glucose_mmol_l: float
    metrics: AnalysisMetrics
    events: list[AnalysisEvent]
    next_state: dict[str, Any]
