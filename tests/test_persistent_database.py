import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app.api.schemas.notification import NotificationStatus
from app.api.schemas.reading import Reading
from app.db.database import Database
from app.services.reading_processor import ReadingProcessor
from app.services.reading_store import ReadingConflictError
from external.kirill_db import db as models


class PersistentDatabaseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "integration.sqlite3"
        self.database_url = f"sqlite:///{self.database_path}"
        self.database = Database(self.database_url)
        self.database.initialize()
        self.processor = ReadingProcessor(database=self.database)

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def make_reading(
        run_id: str,
        source_index: int,
        timestamp: str,
        value: float,
    ) -> Reading:
        return Reading(
            run_id=run_id,
            source_index=source_index,
            measured_at=timestamp,
            glucose_mmol_l=value,
        )

    def test_complete_cycle_persists_and_recovers_after_restart(self) -> None:
        run_id = "persisted-cycle"
        start = self.make_reading(
            run_id, 0, "2026-11-12T12:00:00+03:00", 10.2
        )
        self.assertTrue(self.processor.add_and_analyze(start))

        events = self.processor.list_events(run_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].phase.value, "started")
        self.assertEqual(events[0].priority_hint, "info")
        with self.database.session() as session:
            stored_event = session.execute(
                select(models.GlucoseEvent)
            ).scalar_one()
            self.assertEqual(stored_event.event_uid, events[0].event_id)
        self.assertEqual(
            self.processor.list_notifications(run_id)[0].event_id,
            events[0].event_id,
        )

        notification = self.processor.list_notifications(run_id)[0]
        acknowledged = self.processor.acknowledge_notification(notification.id)
        self.assertEqual(acknowledged.status, NotificationStatus.READ)
        self.assertIsNotNone(acknowledged.acknowledged_at)

        self.database.dispose()
        self.database = Database(self.database_url)
        self.processor = ReadingProcessor(database=self.database)
        recovered = self.processor.state_for(run_id)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["band"]["event_id"], events[0].event_id)

        end = self.make_reading(
            run_id, 1, "2026-11-12T12:05:00+03:00", 9.5
        )
        self.assertTrue(self.processor.add_and_analyze(end))
        recovered_events = self.processor.list_events(run_id)
        self.assertEqual(
            [(event.event_id, event.phase.value) for event in recovered_events],
            [(events[0].event_id, "started"), (events[0].event_id, "ended")],
        )
        self.assertEqual(
            self.processor.list_notifications(run_id)[0].status,
            NotificationStatus.READ,
        )
        self.assertEqual(len(self.processor.list_readings(run_id)), 2)

    def test_duplicate_is_idempotent_and_conflict_is_rejected(self) -> None:
        first = self.make_reading(
            "duplicate-run", 0, "2026-11-12T12:00:00+03:00", 10.2
        )
        self.assertTrue(self.processor.add_and_analyze(first))
        self.assertFalse(self.processor.add_and_analyze(first))
        with self.assertRaises(ReadingConflictError):
            self.processor.add_and_analyze(
                self.make_reading(
                    "duplicate-run", 0, "2026-11-12T12:00:00+03:00", 9.8
                )
            )
        self.assertEqual(len(self.processor.list_events("duplicate-run")), 1)
        self.assertEqual(len(self.processor.list_notifications("duplicate-run")), 1)
        self.assertEqual(len(self.processor.list_readings("duplicate-run")), 1)

    def test_analysis_state_and_multiple_events_survive_restart(self) -> None:
        run_id = "state-recovery"
        self.processor.add_and_analyze(
            self.make_reading(run_id, 0, "2026-11-12T12:00:00+03:00", 8.0)
        )
        self.processor.add_and_analyze(
            self.make_reading(run_id, 1, "2026-11-12T12:05:00+03:00", 9.3)
        )

        with self.database.session() as session:
            simulation_run = session.query(models.SimulationRun).one()
            state = session.get(models.AnalysisState, simulation_run.id)
            self.assertEqual(state.rise_count, 1)
            self.assertIsNone(state.cur_band)

        self.database.dispose()
        self.database = Database(self.database_url)
        self.processor = ReadingProcessor(database=self.database)
        self.processor.add_and_analyze(
            self.make_reading(run_id, 2, "2026-11-12T12:10:00+03:00", 10.6)
        )
        events = self.processor.list_events(run_id)
        self.assertEqual(
            {(event.event_type, event.phase.value) for event in events},
            {
                ("demo_above_range", "started"),
                ("demo_rapid_rise", "started"),
            },
        )
        with self.database.session() as session:
            simulation_run = session.query(models.SimulationRun).one()
            recovered_state = session.get(models.AnalysisState, simulation_run.id)
            self.assertEqual(recovered_state.cur_band["event_type"], "demo_above_range")
            self.assertEqual(recovered_state.cur_trend["event_type"], "demo_rapid_rise")

        self.database.dispose()
        self.database = Database(self.database_url)
        self.processor = ReadingProcessor(database=self.database)
        self.processor.add_and_analyze(
            self.make_reading(run_id, 3, "2026-11-12T12:15:00+03:00", 10.7)
        )
        trend_events = [
            event for event in self.processor.list_events(run_id)
            if event.event_type == "demo_rapid_rise"
        ]
        self.assertEqual([event.phase.value for event in trend_events], ["started", "ended"])
        self.assertEqual(trend_events[0].event_id, trend_events[1].event_id)

    def test_run_data_is_isolated(self) -> None:
        timestamp = "2026-11-12T12:00:00+03:00"
        self.processor.add_and_analyze(
            self.make_reading("run-a", 0, timestamp, 10.2)
        )
        self.processor.add_and_analyze(
            self.make_reading("run-b", 0, timestamp, 8.0)
        )
        self.assertEqual(len(self.processor.list_readings("run-a")), 1)
        self.assertEqual(len(self.processor.list_readings("run-b")), 1)
        self.assertEqual(len(self.processor.list_events("run-a")), 1)
        self.assertEqual(self.processor.list_events("run-b"), [])


if __name__ == "__main__":
    unittest.main()
