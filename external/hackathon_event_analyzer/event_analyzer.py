"""Hackathon: demonstrational CGM event detection (not medical alerting).

Pure Python 3.10+ module. No third-party dependencies.

Main contract:
    analyze_measurement(measurement, history, state, rules) -> JSON-serializable dict

The module must never be used to suppress or delay native CGM critical alarms.
"""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

# All thresholds are FOR SIMULATION ONLY, not medical recommendations.
DEFAULT_RULES: dict[str, float | int] = {
    "lower_enter": 4.0,      # mmol/L, demonstrational threshold
    "lower_exit": 4.4,       # hysteresis for ending episode
    "upper_exit": 9.6,
    "upper_enter": 10.0,
    "rapid_rate": 0.25,      # mmol/L/min, demo-only trend detection
    "confirm_intervals": 2, # successive intervals to confirm trend
    "max_gap_minutes": 15.0,
}

BAND_BELOW = "demo_below_range"
BAND_ABOVE = "demo_above_range"
TREND_UP = "demo_rapid_rise"
TREND_DOWN = "demo_rapid_fall"
DATA_GAP = "data_gap"


def _parse_time(value: Any) -> datetime:
    """Require ISO 8601 time with an explicit timezone offset."""
    if not isinstance(value, str):
        raise ValueError("measured_at must be an ISO 8601 string with offset")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid measured_at: {value!r}") from exc
    if dt.utcoffset() is None:
        raise ValueError("measured_at requires an explicit offset (e.g. +03:00)")
    return dt


def _validate_value(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("glucose_mmol_l must be a numeric value")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("glucose_mmol_l must be numeric") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError("glucose_mmol_l must be a finite positive number")
    return number


def validate_rules(rules: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_RULES)
    if rules is not None:
        unknown = set(rules) - set(cfg)
        if unknown:
            raise ValueError(f"Unknown rules: {', '.join(sorted(unknown))}")
        cfg.update(rules)
    try:
        bounds = [float(cfg[k]) for k in ("lower_enter", "lower_exit", "upper_exit", "upper_enter")]
        rapid = float(cfg["rapid_rate"])
        gap = float(cfg["max_gap_minutes"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Rules must contain numeric thresholds") from exc
    if not all(map(isfinite, [*bounds, rapid, gap])):
        raise ValueError("Rules must be finite")
    if not (0 < bounds[0] < bounds[1] < bounds[2] < bounds[3]):
        raise ValueError("Require lower_enter < lower_exit < upper_exit < upper_enter")
    if rapid <= 0 or gap <= 0:
        raise ValueError("rapid_rate and max_gap_minutes must be positive")
    count = cfg["confirm_intervals"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("confirm_intervals must be a positive integer")
    for k, v in zip(("lower_enter", "lower_exit", "upper_exit", "upper_enter"), bounds):
        cfg[k] = v
    cfg["rapid_rate"] = rapid
    cfg["max_gap_minutes"] = gap
    return cfg


def _new_state(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "last_measured_at": None,
        "last_value_mmol_l": None,
        "band": None,  # {event_type, started_at, event_id}
        "trend": None,
        "rise_count": 0,
        "fall_count": 0,
    }


def _episode_id(run_id: str, event_type: str, started_at: str) -> str:
    # Stable event IDs simplify updates in the database.
    return str(uuid5(NAMESPACE_URL, f"hackathon-demo:{run_id}:{event_type}:{started_at}"))


def _event(
    *, run_id: str, kind: str, phase: str, at: str,
    value: float, episode: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started = episode["started_at"] if episode else at
    event_id = episode["event_id"] if episode else _episode_id(run_id, kind, started)
    return {
        "event_id": event_id,
        "run_id": run_id,
        "event_type": kind,
        "phase": phase,               # started | ended | interrupted | observed
        "origin": "demonstration_algorithm",
        "priority_hint": "info",     # NOT a medical urgency level
        "started_at": started,
        "measured_at": at,
        "ended_at": at if phase in ("ended", "interrupted") else None,
        "glucose_mmol_l": value,
        "details": dict(details or {}),
    }


def _get_previous(history: Sequence[Mapping[str, Any]] | None, state: Mapping[str, Any]):
    """State is authoritative; history provides an optional cold-start reference."""
    if state["last_measured_at"] is not None:
        return _parse_time(state["last_measured_at"]), _validate_value(state["last_value_mmol_l"])
    if history:
        # Time-based ordering handles arbitrary history list order.
        prev = max(history, key=lambda m: _parse_time(m["measured_at"]))
        return _parse_time(prev["measured_at"]), _validate_value(prev["glucose_mmol_l"])
    return None, None


def analyze_measurement(
    measurement: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]] | None = None,
    state: Mapping[str, Any] | None = None,
    rules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze one simulated CGM reading without database or network access.

    measurement: {run_id, source_index, measured_at, glucose_mmol_l}
    history: earlier readings; optional for stream processing, useful for cold start
    state: JSON dictionary returned by previous call (must be retained per run_id)
    rules: test-only demonstration thresholds (see DEFAULT_RULES)

    Returns {status, run_id, source_index, measured_at, glucose_mmol_l,
             metrics, events, next_state}. Do not use for patient care.
    """
    cfg = validate_rules(rules)
    if not isinstance(measurement, Mapping):
        raise ValueError("measurement must be a dictionary")
    run_id = measurement.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a nonempty string")
    if "source_index" not in measurement:
        raise ValueError("source_index is required")
    source_index = measurement["source_index"]
    if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
        raise ValueError("source_index must be a non-negative integer")
    timestamp = _parse_time(measurement.get("measured_at"))
    value = _validate_value(measurement.get("glucose_mmol_l"))
    at = timestamp.isoformat()

    current = _new_state(run_id)
    if state is not None:
        if state.get("run_id") != run_id:
            raise ValueError("state.run_id does not match measurement.run_id")
        current.update({k: state.get(k, v) for k, v in current.items()})
    prev_dt, prev_value = _get_previous(history, current)

    base = {
        "run_id": run_id,
        "source_index": source_index,
        "measured_at": at,
        "glucose_mmol_l": value,
        "events": [],
        "metrics": {"delta_mmol_l": None, "rate_mmol_l_min": None,
                    "minutes_since_previous": None},
    }
    if prev_dt is not None:
        minutes = (timestamp - prev_dt).total_seconds() / 60.0
        base["metrics"]["minutes_since_previous"] = minutes
        if minutes <= 0:
            base.update(status="duplicate" if minutes == 0 else "out_of_order", next_state=current)
            return base
        delta = value - prev_value
        base["metrics"]["delta_mmol_l"] = round(delta, 4)
        if minutes <= float(cfg["max_gap_minutes"]):
            base["metrics"]["rate_mmol_l_min"] = round(delta / minutes, 5)

    events = base["events"]
    minutes = base["metrics"]["minutes_since_previous"]
    gap = minutes is not None and minutes > cfg["max_gap_minutes"]

    if gap:
        # No assumption that a high/low episode resolved during missing data.
        for key in ("band", "trend"):
            if current[key] is not None:
                episode = current[key]
                events.append(_event(run_id=run_id, kind=episode["event_type"],
                                     phase="interrupted", at=at, value=value, episode=episode,
                                     details={"reason": "gap_in_measurements"}))
                current[key] = None
        current["rise_count"] = 0
        current["fall_count"] = 0
        events.append(_event(run_id=run_id, kind=DATA_GAP, phase="observed",
                             at=at, value=value,
                             details={"gap_minutes": round(minutes, 2),
                                      "previous_at": prev_dt.isoformat()}))

    # Range episodes use hysteresis, to reduce repeated on/off transitions.
    active_band = current["band"]
    if active_band is not None:
        kind = active_band["event_type"]
        should_end = ((kind == BAND_BELOW and value >= cfg["lower_exit"]) or
                      (kind == BAND_ABOVE and value <= cfg["upper_exit"]))
        if should_end:
            events.append(_event(run_id=run_id, kind=kind, phase="ended",
                                 at=at, value=value, episode=active_band))
            current["band"] = None

    if current["band"] is None:
        kind = None
        if value <= cfg["lower_enter"]:
            kind = BAND_BELOW
        elif value >= cfg["upper_enter"]:
            kind = BAND_ABOVE
        if kind is not None:
            episode = {"event_type": kind, "started_at": at,
                       "event_id": _episode_id(run_id, kind, at)}
            current["band"] = episode
            events.append(_event(run_id=run_id, kind=kind, phase="started",
                                 at=at, value=value, episode=episode))

    # Trend needs consecutive valid intervals; gap cannot be crossed.
    rate = base["metrics"]["rate_mmol_l_min"]
    trend_kind = None
    if rate is not None and rate >= cfg["rapid_rate"]:
        trend_kind = TREND_UP
    elif rate is not None and rate <= -cfg["rapid_rate"]:
        trend_kind = TREND_DOWN

    if trend_kind == TREND_UP:
        current["rise_count"] += 1
        current["fall_count"] = 0
    elif trend_kind == TREND_DOWN:
        current["fall_count"] += 1
        current["rise_count"] = 0
    else:
        current["fall_count"] = 0
        current["rise_count"] = 0

    active_trend = current["trend"]
    if active_trend is not None and active_trend["event_type"] != trend_kind:
        events.append(_event(run_id=run_id, kind=active_trend["event_type"],
                             phase="ended", at=at, value=value, episode=active_trend))
        current["trend"] = None

    should_start = ((trend_kind == TREND_UP and current["rise_count"] >= cfg["confirm_intervals"]) or
                    (trend_kind == TREND_DOWN and current["fall_count"] >= cfg["confirm_intervals"]))
    if current["trend"] is None and trend_kind is not None and should_start:
        episode = {"event_type": trend_kind, "started_at": at,
                   "event_id": _episode_id(run_id, trend_kind, at)}
        current["trend"] = episode
        events.append(_event(run_id=run_id, kind=trend_kind, phase="started",
                             at=at, value=value, episode=episode,
                             details={"rate_mmol_l_min": rate}))

    current["last_measured_at"] = at
    current["last_value_mmol_l"] = value
    base.update(status="processed", next_state=current)
    return base
