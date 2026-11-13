import unittest
import uuid

from pydantic import ValidationError

from app.api.schemas.analysis import AnalysisEvent, AnalysisOutput
from app.api.schemas.reading import Reading
from app.integrations.algorithm import DEMO_RULES, analyze_reading
from app.services.event_store import InMemoryEventStore
from app.services.reading_processor import ReadingProcessor
from app.services.reading_store import reading_store


def reading(
    run_id: str,
    source_index: int,
    measured_at: str,
    glucose_mmol_l: float,
) -> Reading:
    return Reading(
        run_id=run_id,
        source_index=source_index,
        measured_at=measured_at,
        glucose_mmol_l=glucose_mmol_l,
    )


class AnalysisIntegrationTests(unittest.TestCase):
    def test_adapter_preserves_measurement_timestamp_and_values(self) -> None:
        current = reading("adapter-test", 2, "2026-11-12T10:05:00+03:00", 10.2)
        previous = reading("adapter-test", 1, "2026-11-12T10:00:00+03:00", 8.0)

        result = analyze_reading(current, [previous])

        self.assertEqual(result.status, "processed")
        self.assertEqual(result.run_id, current.run_id)
        self.assertEqual(result.source_index, current.source_index)
        self.assertEqual(result.measured_at, current.measured_at.isoformat())
        self.assertEqual(result.metrics.delta_mmol_l, 2.2)
        self.assertEqual(result.events[0].measured_at, current.measured_at.isoformat())

    def test_all_events_are_preserved_and_are_not_notifications(self) -> None:
        run_id = f"multi-event-{uuid.uuid4()}"
        state = None
        measurements = [
            reading(run_id, 0, "2026-11-12T10:00:00+03:00", 8.0),
            reading(run_id, 1, "2026-11-12T10:05:00+03:00", 9.3),
            reading(run_id, 2, "2026-11-12T10:10:00+03:00", 10.6),
        ]
        results = []
        for current in measurements:
            result = analyze_reading(current, [], state=state, rules=DEMO_RULES)
            state = result.next_state
            results.append(result)

        events = results[-1].events
        self.assertEqual(
            {(event.event_type, event.phase.value) for event in events},
            {("demo_above_range", "started"), ("demo_rapid_rise", "started")},
        )
        self.assertTrue(all(isinstance(event, AnalysisEvent) for event in events))
        self.assertTrue(all(not hasattr(event, "create_information_notification") for event in events))

    def test_state_is_isolated_by_run_id(self) -> None:
        processor = ReadingProcessor(events=InMemoryEventStore())
        first_run = f"state-a-{uuid.uuid4()}"
        second_run = f"state-b-{uuid.uuid4()}"
        first_reading = reading(first_run, 1, "2026-11-12T10:00:00+03:00", 10.2)
        second_reading = reading(second_run, 1, "2026-11-12T10:00:00+03:00", 5.0)

        processor.add_and_analyze(first_reading)
        processor.add_and_analyze(second_reading)

        first_state = processor.state_for(first_run)
        second_state = processor.state_for(second_run)
        self.assertIsNotNone(first_state)
        self.assertIsNotNone(second_state)
        self.assertEqual(first_state["last_value_mmol_l"], 10.2)
        self.assertEqual(second_state["last_value_mmol_l"], 5.0)

    def test_duplicate_reading_does_not_update_state_or_add_events(self) -> None:
        processor_events = InMemoryEventStore()
        processor = ReadingProcessor(events=processor_events)
        run_id = f"duplicate-{uuid.uuid4()}"
        first = reading(run_id, 1, "2026-11-12T10:00:00+03:00", 10.2)

        self.assertTrue(processor.add_and_analyze(first))
        first_state = processor.state_for(run_id)
        first_events = processor_events.list(run_id)
        self.assertFalse(processor.add_and_analyze(first))

        self.assertEqual(processor.state_for(run_id), first_state)
        self.assertEqual(processor_events.list(run_id), first_events)

    def test_event_result_requires_complete_event_identity(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisOutput.model_validate(
                {
                    "status": "processed",
                    "run_id": "demo-001",
                    "source_index": 1,
                    "measured_at": "2026-11-12T10:00:00+03:00",
                    "glucose_mmol_l": 10.2,
                    "metrics": {
                        "delta_mmol_l": None,
                        "rate_mmol_l_min": None,
                        "minutes_since_previous": None,
                    },
                    "events": [
                        {
                            "run_id": "demo-001",
                            "event_type": "demo_above_range",
                            "phase": "started",
                        }
                    ],
                    "next_state": {"run_id": "demo-001"},
                }
            )

    def test_out_of_order_result_does_not_replace_state(self) -> None:
        run_id = f"ordering-{uuid.uuid4()}"
        first = reading(run_id, 2, "2026-11-12T10:10:00+03:00", 8.0)
        result = analyze_reading(first, [])
        earlier = reading(run_id, 1, "2026-11-12T10:05:00+03:00", 9.0)

        out_of_order = analyze_reading(earlier, [], state=result.next_state)

        self.assertEqual(out_of_order.status, "out_of_order")
        self.assertEqual(out_of_order.next_state, result.next_state)
        self.assertEqual(out_of_order.events, [])


if __name__ == "__main__":
    unittest.main()
