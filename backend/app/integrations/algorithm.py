from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from external.hackathon_event_analyzer.event_analyzer import validate_rules

from app.api.schemas.analysis import AnalysisOutput
from app.api.schemas.reading import Reading


DEMO_RULES = validate_rules()


def _reading_to_measurement(reading: Reading) -> dict[str, object]:
    return {
        "run_id": reading.run_id,
        "source_index": reading.source_index,
        "measured_at": reading.measured_at.isoformat(),
        "glucose_mmol_l": reading.glucose_mmol_l,
    }


def analyze_reading(
    current: Reading,
    history: list[Reading],
    state: Mapping[str, Any] | None = None,
    rules: Mapping[str, Any] | None = None,
) -> AnalysisOutput:
    from external.hackathon_event_analyzer.event_analyzer import analyze_measurement

    effective_rules = validate_rules(rules if rules is not None else DEMO_RULES)
    raw_result = analyze_measurement(
        measurement=_reading_to_measurement(current),
        history=[_reading_to_measurement(reading) for reading in history],
        state=deepcopy(dict(state)) if state is not None else None,
        rules=effective_rules,
    )
    result = AnalysisOutput.model_validate(raw_result)
    if result.run_id != current.run_id or result.source_index != current.source_index:
        raise ValueError("Analyzer result does not match the current reading")
    if result.next_state.get("run_id") != current.run_id:
        raise ValueError("Analyzer next_state does not match the current run")
    if any(event.run_id != current.run_id for event in result.events):
        raise ValueError("Analyzer returned an event for a different run")
    return result
