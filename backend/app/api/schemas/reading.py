from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    source_index: int = Field(ge=0, strict=True)
    measured_at: datetime
    glucose_mmol_l: float = Field(gt=0, allow_inf_nan=False, strict=True)

    @field_validator("run_id")
    @classmethod
    def run_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run_id must not be blank")
        return value

    @field_validator("measured_at", mode="before")
    @classmethod
    def measured_at_must_be_a_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("measured_at must be an ISO 8601 string")
        return value

    @field_validator("measured_at")
    @classmethod
    def measured_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must include a timezone")
        return value
