from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isclose, isfinite
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.api.schemas.analysis import AnalysisEvent, EventPhase
from app.api.schemas.reading import Reading

POLICY_STATE_KEY = "_notification_policy_state"

_RANGE_EVENT_TYPES = {"demo_above_range", "demo_below_range"}
_TREND_EVENT_TYPES = {"demo_rapid_rise", "demo_rapid_fall"}


@dataclass(frozen=True)
class NotificationPolicyConfig:
    """Demonstration-only notification settings; not clinically validated."""

    change_window_minutes: float = 10.0
    rapid_change_mmol_l: float = 1.1
    trend_reset_intervals: int = 2
    range_recovery_readings: int = 3
    low_bypass_mmol_l: float = 3.0
    low_bypass_reset_mmol_l: float = 3.2

    def __post_init__(self) -> None:
        if (
            not isfinite(self.change_window_minutes)
            or self.change_window_minutes <= 0
            or not isfinite(self.rapid_change_mmol_l)
            or self.rapid_change_mmol_l <= 0
            or not isfinite(self.low_bypass_mmol_l)
            or not isfinite(self.low_bypass_reset_mmol_l)
            or self.low_bypass_mmol_l <= 0
            or self.low_bypass_reset_mmol_l <= self.low_bypass_mmol_l
        ):
            raise ValueError("Notification policy thresholds must be finite and positive")
        if (
            isinstance(self.trend_reset_intervals, bool)
            or not isinstance(self.trend_reset_intervals, int)
            or self.trend_reset_intervals < 1
            or isinstance(self.range_recovery_readings, bool)
            or not isinstance(self.range_recovery_readings, int)
            or self.range_recovery_readings < 1
        ):
            raise ValueError("Notification policy confirmation counts must be positive integers")


@dataclass(frozen=True)
class NotificationPolicyResult:
    notifications: list[AnalysisEvent]
    generated_events: list[AnalysisEvent]
    state: dict[str, Any]


def _initial_state(active_band: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "active_range_type": (
            active_band.get("event_type") if active_band is not None else None
        ),
        "active_range_event_id": (
            active_band.get("event_id") if active_band is not None else None
        ),
        "active_range_acknowledged": False,
        "active_range_ack_measured_at": None,
        "range_recovery_count": 0,
        "latched_trend_type": None,
        "latched_trend_event_id": None,
        "latched_trend_acknowledged": False,
        "latched_trend_ack_measured_at": None,
        "trend_reset_count": 0,
        "critical_low_latched": False,
        "low_bypass_recovery_count": 0,
    }


def _parse_time(value: datetime | str) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Reading timestamps must include a timezone")
    return result


def _make_policy_event(
    reading: Reading,
    event_type: str,
    *,
    reason: str,
    config: NotificationPolicyConfig,
    priority_hint: str = "info",
) -> AnalysisEvent:
    measured_at = reading.measured_at.isoformat()
    event_id = str(
        uuid5(
            NAMESPACE_URL,
            f"cgm-assistant:notification-policy:{reading.run_id}:{event_type}:{measured_at}:{reason}",
        )
    )
    return AnalysisEvent(
        event_id=event_id,
        run_id=reading.run_id,
        event_type=event_type,
        phase=EventPhase.STARTED,
        origin="demonstration_notification_policy",
        priority_hint=priority_hint,
        started_at=measured_at,
        ended_at=None,
        measured_at=measured_at,
        glucose_mmol_l=reading.glucose_mmol_l,
        details={
            "policy_reason": reason,
            "change_window_minutes": config.change_window_minutes,
            "rapid_change_mmol_l": config.rapid_change_mmol_l,
        },
    )


def _trend_candidate(
    reading: Reading,
    history: Sequence[Reading],
    config: NotificationPolicyConfig,
    since: datetime | None = None,
) -> str | None:
    current_time = _parse_time(reading.measured_at)
    start_time = current_time - timedelta(minutes=config.change_window_minutes)
    points = [
        item
        for item in history
        if item.run_id == reading.run_id
        and start_time <= _parse_time(item.measured_at) < current_time
        and (since is None or _parse_time(item.measured_at) >= since)
    ]
    points.sort(key=lambda item: _parse_time(item.measured_at))
    points.append(reading)
    points = points[-3:]
    if len(points) < 2:
        return None

    times = [_parse_time(item.measured_at) for item in points]
    values = [item.glucose_mmol_l for item in points]
    if any(later <= earlier for earlier, later in zip(times, times[1:])):
        return None

    change = values[-1] - values[0]
    if (
        (
            change >= config.rapid_change_mmol_l
            or isclose(change, config.rapid_change_mmol_l, abs_tol=1e-9)
        )
        and all(later >= earlier for earlier, later in zip(values, values[1:]))
    ):
        return "demo_rapid_rise"
    if (
        (
            change <= -config.rapid_change_mmol_l
            or isclose(change, -config.rapid_change_mmol_l, abs_tol=1e-9)
        )
        and all(later <= earlier for earlier, later in zip(values, values[1:]))
    ):
        return "demo_rapid_fall"
    return None


def apply_notification_policy(
    *,
    reading: Reading,
    history: Sequence[Reading],
    events: Sequence[AnalysisEvent],
    previous_state: Mapping[str, Any] | None,
    algorithm_state: Mapping[str, Any],
    rules: Mapping[str, Any],
    config: NotificationPolicyConfig,
) -> NotificationPolicyResult:
    old_state = previous_state.get(POLICY_STATE_KEY) if previous_state else None
    active_band = previous_state.get("band") if previous_state is not None else None
    state = (
        deepcopy(dict(old_state))
        if isinstance(old_state, Mapping)
        else _initial_state(active_band if isinstance(active_band, Mapping) else None)
    )
    state.setdefault("active_range_type", None)
    state.setdefault("active_range_event_id", None)
    state.setdefault("active_range_acknowledged", False)
    state.setdefault("active_range_ack_measured_at", None)
    state.setdefault("range_recovery_count", 0)
    state.setdefault("latched_trend_type", None)
    state.setdefault("latched_trend_event_id", None)
    state.setdefault("latched_trend_acknowledged", False)
    state.setdefault("latched_trend_ack_measured_at", None)
    state.setdefault("trend_reset_count", 0)
    state.setdefault("critical_low_latched", False)
    state.setdefault("low_bypass_recovery_count", 0)

    ordered_history = sorted(
        (
            item
            for item in history
            if item.run_id == reading.run_id
            and _parse_time(item.measured_at) < _parse_time(reading.measured_at)
        ),
        key=lambda item: _parse_time(item.measured_at),
    )
    previous_reading = ordered_history[-1] if ordered_history else None
    gap_minutes = (
        (_parse_time(reading.measured_at) - _parse_time(previous_reading.measured_at))
        .total_seconds()
        / 60
        if previous_reading is not None
        else None
    )

    latched_trend = state["latched_trend_type"]
    if gap_minutes is not None and gap_minutes > config.change_window_minutes:
        state["latched_trend_type"] = None
        state["trend_reset_count"] = 0
    elif latched_trend and previous_reading is not None:
        delta = reading.glucose_mmol_l - previous_reading.glucose_mmol_l
        reset_direction = (
            delta <= 0 if latched_trend == "demo_rapid_rise" else delta >= 0
        )
        state["trend_reset_count"] = (
            state["trend_reset_count"] + 1 if reset_direction else 0
        )
        if state["trend_reset_count"] >= config.trend_reset_intervals:
            state["latched_trend_type"] = None
            state["trend_reset_count"] = 0

    active_range_type = state["active_range_type"]
    if active_range_type == "demo_above_range":
        recovered = reading.glucose_mmol_l <= float(rules["upper_exit"])
    elif active_range_type == "demo_below_range":
        recovered = reading.glucose_mmol_l >= float(rules["lower_exit"])
    else:
        recovered = False

    if active_range_type is not None:
        state["range_recovery_count"] = (
            state["range_recovery_count"] + 1 if recovered else 0
        )
        if state["range_recovery_count"] >= config.range_recovery_readings:
            state["active_range_type"] = None
            state["active_range_event_id"] = None
            state["active_range_acknowledged"] = False
            state["active_range_ack_measured_at"] = None
            state["range_recovery_count"] = 0

    generated_events: list[AnalysisEvent] = []
    notifications: list[AnalysisEvent] = []

    if reading.glucose_mmol_l >= config.low_bypass_reset_mmol_l:
        state["low_bypass_recovery_count"] += 1
        if state["low_bypass_recovery_count"] >= config.range_recovery_readings:
            state["critical_low_latched"] = False
            state["low_bypass_recovery_count"] = 0
    else:
        state["low_bypass_recovery_count"] = 0
    low_bypass = (
        reading.glucose_mmol_l < config.low_bypass_mmol_l
        and not state["critical_low_latched"]
    )
    if low_bypass:
        event = _make_policy_event(
            reading,
            "demo_below_range",
            reason="demonstration_low_bypass",
            config=config,
            priority_hint="warning",
        )
        generated_events.append(event)
        notifications.append(event)
        state["critical_low_latched"] = True
        state["low_bypass_recovery_count"] = 0
        state["active_range_type"] = "demo_below_range"
        state["active_range_event_id"] = event.event_id
        state["active_range_acknowledged"] = False
        state["active_range_ack_measured_at"] = None
        state["range_recovery_count"] = 0

    started_trends = {
        event.event_type: event
        for event in events
        if event.phase == EventPhase.STARTED
        and event.event_type in _TREND_EVENT_TYPES
    }
    band_starts = [
        event
        for event in events
        if event.phase == EventPhase.STARTED
        and event.event_type in _RANGE_EVENT_TYPES
    ]
    for event in band_starts:
        if low_bypass and event.event_type == "demo_below_range":
            state["active_range_type"] = event.event_type
            state["active_range_event_id"] = next(
                (
                    item.event_id
                    for item in notifications
                    if item.event_type == event.event_type
                ),
                event.event_id,
            )
            state["active_range_acknowledged"] = False
            state["active_range_ack_measured_at"] = None
            state["range_recovery_count"] = 0
        elif state["active_range_type"] != event.event_type:
            notifications.append(event)
            state["active_range_type"] = event.event_type
            state["active_range_event_id"] = event.event_id
            state["active_range_acknowledged"] = False
            state["active_range_ack_measured_at"] = None
            state["range_recovery_count"] = 0

    acknowledgement_times = [
        _parse_time(value)
        for acknowledged, value in (
            (
                state["active_range_acknowledged"],
                state["active_range_ack_measured_at"],
            ),
            (
                state["latched_trend_acknowledged"],
                state["latched_trend_ack_measured_at"],
            ),
        )
        if acknowledged and value is not None
    ]
    candidate_since = max(acknowledgement_times, default=None)
    trend_policy_enabled = state["active_range_type"] is not None and (
        state["active_range_acknowledged"]
        or (
            state["active_range_type"] == "demo_below_range"
            and reading.glucose_mmol_l < config.low_bypass_mmol_l
        )
    )
    candidate = (
        _trend_candidate(
            reading,
            ordered_history,
            config,
            since=candidate_since,
        )
        if trend_policy_enabled
        else None
    )
    trend_types = set(started_trends)
    if candidate is not None:
        trend_types.add(candidate)

    active_range_type = state["active_range_type"]
    for event_type in ("demo_rapid_rise", "demo_rapid_fall"):
        if event_type not in trend_types:
            continue
        if (
            active_range_type == "demo_above_range"
            and event_type == "demo_rapid_fall"
        ) or (
            active_range_type == "demo_below_range"
            and event_type == "demo_rapid_rise"
        ):
            continue
        if (
            active_range_type == "demo_above_range"
            and event_type == "demo_rapid_rise"
            and not state["active_range_acknowledged"]
        ) or (
            active_range_type == "demo_below_range"
            and event_type == "demo_rapid_fall"
            and not state["active_range_acknowledged"]
            and reading.glucose_mmol_l >= config.low_bypass_mmol_l
        ):
            continue
        if state["latched_trend_type"] == event_type:
            continue
        event = started_trends.get(event_type)
        if event is None:
            event = _make_policy_event(
                reading,
                event_type,
                reason="demonstration_change_threshold",
                config=config,
            )
            generated_events.append(event)
        notifications.append(event)
        state["latched_trend_type"] = event_type
        state["latched_trend_event_id"] = event.event_id
        state["latched_trend_acknowledged"] = False
        state["latched_trend_ack_measured_at"] = None
        state["trend_reset_count"] = 0

    low_bypass_notifications = [
        event
        for event in notifications
        if event.details.get("policy_reason") == "demonstration_low_bypass"
    ]
    all_started_events = [
        event
        for event in (*events, *generated_events)
        if event.phase == EventPhase.STARTED
        and event.event_type == "demo_rapid_fall"
        and event.run_id == reading.run_id
        and event.measured_at == reading.measured_at.isoformat()
    ]
    for low_event in low_bypass_notifications:
        related_ids = sorted({event.event_id for event in all_started_events})
        if related_ids:
            low_event.details["related_event_ids"] = related_ids
            low_event.details["related_event_types"] = ["demo_rapid_fall"]
            notifications = [
                event
                for event in notifications
                if event.event_id not in related_ids
            ]

    current_band = algorithm_state.get("band")
    if (
        state["active_range_type"] is None
        and isinstance(current_band, Mapping)
    ):
        state["active_range_type"] = current_band.get("event_type")
        state["active_range_event_id"] = current_band.get("event_id")

    return NotificationPolicyResult(notifications, generated_events, state)
