from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
import tempfile
import unittest

from app.api.schemas.notification import NotificationStatus
from app.api.schemas.reading import Reading
from app.db.database import Database
from app.services.reading_processor import ReadingProcessor


class NotificationConcurrencyTests(unittest.TestCase):
    def test_ack_and_reading_write_are_serialized_across_database_engines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'concurrency.sqlite3'}"
            writer_database = Database(database_url)
            acknowledger_database = Database(database_url)
            writer_database.initialize()
            writer = ReadingProcessor(database=writer_database)
            acknowledger = ReadingProcessor(database=acknowledger_database)
            base_time = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    for iteration in range(100):
                        run_id = f"concurrent-run-{iteration}"
                        first = Reading(
                            run_id=run_id,
                            source_index=0,
                            measured_at=base_time.isoformat(),
                            glucose_mmol_l=11.8,
                        )
                        self.assertTrue(writer.add_and_analyze(first))
                        initial_notifications = writer.list_notifications(run_id)
                        self.assertEqual(len(initial_notifications), 1)

                        barrier = Barrier(2)

                        def acknowledge() -> None:
                            barrier.wait(timeout=5)
                            acknowledger.acknowledge_notification(
                                initial_notifications[0].id
                            )

                        def add_reading() -> None:
                            barrier.wait(timeout=5)
                            writer.add_and_analyze(
                                Reading(
                                    run_id=run_id,
                                    source_index=1,
                                    measured_at=(
                                        base_time + timedelta(minutes=5)
                                    ).isoformat(),
                                    glucose_mmol_l=11.9,
                                )
                            )

                        acknowledge_future = executor.submit(acknowledge)
                        reading_future = executor.submit(add_reading)
                        acknowledge_future.result(timeout=10)
                        reading_future.result(timeout=10)

                        notifications = writer.list_notifications(run_id)
                        self.assertEqual(len(notifications), 1)
                        self.assertEqual(
                            notifications[0].status,
                            NotificationStatus.READ,
                        )
                        readings = writer.list_readings(run_id)
                        self.assertEqual(len(readings), 2)
                        policy_state = writer.state_for(run_id)[
                            "_notification_policy_state"
                        ]
                        self.assertTrue(
                            policy_state["active_range_acknowledged"]
                        )
            finally:
                writer_database.dispose()
                acknowledger_database.dispose()


if __name__ == "__main__":
    unittest.main()
