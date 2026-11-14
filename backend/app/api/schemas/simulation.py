from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MAX_SPEED_MULTIPLIER = 3600.0
DEFAULT_SPEED_MULTIPLIER = 60.0


class StartSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed_multiplier: float = Field(
        default=DEFAULT_SPEED_MULTIPLIER,
        gt=0,
        le=MAX_SPEED_MULTIPLIER,
        allow_inf_nan=False,
        strict=True,
    )


class SimulationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None
    status: Literal["idle", "running", "paused", "stopped", "completed", "error"]
    total_readings: int
    processed_readings: int
    current_source_index: int
    speed_multiplier: float
    error: str | None


class SimulationRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    started_at: datetime
    processed_readings: int
