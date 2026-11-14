import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.api.schemas.notification import NotificationStatus
from app.api.schemas.reading import Reading
from app.db.database import Database
from app.services.reading_processor import ReadingProcessor
from app.services.reading_store import ReadingConflictError


class NotificationPolicyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "notification-policy.sqlite3"
        self.database = Database(f"sqlite:///{database_path}")
        self.database.initialize()
        self.processor = ReadingProcessor(database=self.database)
        self.base_time = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
        self.indices: dict[str, int] = {}

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    def add(
        self,
        run_id: str,
        minute: int,
        value: float,
        *,
        source_index: int | None = None,
    ) -> bool:
        index = source_index
        if index is None:
            index = self.indices.get(run_id, -1) + 1
        self.indices[run_id] = max(index, self.indices.get(run_id, -1))
        return self.processor.add_and_analyze(
            Reading(
                run_id=run_id,
                source_index=index,
                measured_at=(self.base_time + timedelta(minutes=minute)).isoformat(),
                glucose_mmol_l=value,
            )
        )

    def notifications(self, run_id: str):
        return self.processor.list_notifications(run_id)

    def acknowledge_only_notification(self, run_id: str):
        notifications = self.notifications(run_id)
        self.assertTrue(notifications)
        acknowledged = self.processor.acknowledge_notification(
            notifications[-1].id
        )
        self.assertEqual(acknowledged.status, NotificationStatus.READ)
        self.assertIsNotNone(acknowledged.acknowledged_at)
        return acknowledged

    def test_one_range_episode_is_deduplicated_before_and_after_ack(self) -> None:
        run_id = "same-high-episode"
        self.add(run_id, 0, 11.8)
        self.add(run_id, 5, 12.0)
        self.add(run_id, 35, 12.1)

        self.assertEqual(len(self.notifications(run_id)), 1)
        acknowledged = self.acknowledge_only_notification(run_id)
        self.add(run_id, 40, 11.3)
        self.add(run_id, 45, 10.7)

        notifications = self.notifications(run_id)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].status, NotificationStatus.READ)
        self.assertEqual(notifications[0].id, acknowledged.id)

    def test_new_fast_rise_after_ack_is_not_repeated_during_same_rise(self) -> None:
        run_id = "rise-after-ack"
        self.add(run_id, 0, 11.8)
        self.acknowledge_only_notification(run_id)
        self.add(run_id, 5, 11.3)
        self.add(run_id, 10, 10.7)
        self.add(run_id, 15, 11.2)
        self.add(run_id, 20, 12.0)

        notifications = self.notifications(run_id)
        self.assertEqual(len(notifications), 2)
        rise = [
            event
            for event in self.processor.list_events(run_id)
            if event.event_type == "demo_rapid_rise"
            and event.origin == "demonstration_notification_policy"
        ]
        self.assertEqual(len(rise), 1)
        self.assertEqual(rise[0].measured_at, (self.base_time + timedelta(minutes=20)).isoformat())

        self.add(run_id, 25, 12.5)
        self.add(run_id, 30, 13.1)
        self.assertEqual(len(self.notifications(run_id)), 2)
        self.assertEqual(len(rise), 1)

    def test_new_worsening_is_held_until_current_episode_is_acknowledged(self) -> None:
        run_id = "worsening-needs-ack"
        self.add(run_id, 0, 11.8)
        self.add(run_id, 5, 11.3)
        self.add(run_id, 10, 10.7)
        self.add(run_id, 15, 11.2)
        self.add(run_id, 20, 12.0)
        self.assertEqual(len(self.notifications(run_id)), 1)

        self.acknowledge_only_notification(run_id)
        self.add(run_id, 25, 12.5)
        self.add(run_id, 30, 13.1)
        self.assertEqual(len(self.notifications(run_id)), 2)

    def test_acknowledgement_does_not_replay_a_rise_that_started_before_it(self) -> None:
        run_id = "acknowledged-mid-rise"
        self.add(run_id, 0, 11.8)
        self.add(run_id, 5, 11.3)
        self.add(run_id, 10, 10.7)
        self.add(run_id, 15, 11.2)
        self.add(run_id, 20, 12.0)
        self.assertEqual(len(self.notifications(run_id)), 1)

        self.acknowledge_only_notification(run_id)
        self.add(run_id, 25, 12.5)
        self.assertEqual(len(self.notifications(run_id)), 1)
        self.add(run_id, 30, 13.1)
        self.assertEqual(len(self.notifications(run_id)), 2)

    def test_recovering_low_episode_does_not_repeat_notification(self) -> None:
        run_id = "recovering-low"
        self.add(run_id, 0, 3.8)
        self.acknowledge_only_notification(run_id)
        self.add(run_id, 5, 3.9)
        self.add(run_id, 10, 4.1)
        self.add(run_id, 15, 4.3)

        self.assertEqual(len(self.notifications(run_id)), 1)
        self.assertEqual(
            self.notifications(run_id)[0].status,
            NotificationStatus.READ,
        )

    def test_new_fast_fall_and_low_bypass_are_preserved(self) -> None:
        run_id = "worsening-low"
        self.add(run_id, 0, 3.8)
        self.acknowledge_only_notification(run_id)
        self.add(run_id, 5, 4.1)
        self.add(run_id, 10, 3.5)
        self.add(run_id, 15, 2.9)
        self.add(run_id, 20, 2.7)

        notifications = self.notifications(run_id)
        self.assertEqual(len(notifications), 2)
        events = self.processor.list_events(run_id)
        low_event = next(
            event
            for event in events
            if event.details.get("policy_reason") == "demonstration_low_bypass"
        )
        rapid_fall = next(
            event
            for event in events
            if event.event_type == "demo_rapid_fall"
            and event.origin == "demonstration_notification_policy"
        )
        self.assertEqual(low_event.priority_hint, "warning")
        self.assertEqual(low_event.measured_at, rapid_fall.measured_at)
        self.assertEqual(
            low_event.details["related_event_ids"],
            [rapid_fall.event_id],
        )
        combined = next(
            notification
            for notification in notifications
            if notification.event_id == low_event.event_id
        )
        self.assertIn("низкий уровень глюкозы", combined.title.lower())
        self.assertIn("быстрое снижение", combined.title.lower())
        self.assertIn("быстрое снижение", combined.text.lower())

        self.processor.acknowledge_notification(combined.id)
        combined_policy_state = self.processor.state_for(run_id)[
            "_notification_policy_state"
        ]
        self.assertTrue(combined_policy_state["active_range_acknowledged"])
        self.assertTrue(combined_policy_state["latched_trend_acknowledged"])
        self.add(run_id, 25, 2.6)
        self.assertEqual(len(self.notifications(run_id)), 2)

    def test_low_bypass_is_once_per_low_excursion(self) -> None:
        run_id = "low-bypass-latch"
        self.add(run_id, 0, 3.8)
        self.acknowledge_only_notification(run_id)
        self.add(run_id, 5, 2.9)
        self.add(run_id, 10, 2.7)
        self.assertEqual(len(self.notifications(run_id)), 3)
        low_bypass_events = [
            event
            for event in self.processor.list_events(run_id)
            if event.details.get("policy_reason") == "demonstration_low_bypass"
        ]
        self.assertEqual(len(low_bypass_events), 1)

    def test_low_bypass_requires_stable_recovery_before_rearming(self) -> None:
        run_id = "low-bypass-recovery"
        self.add(run_id, 0, 2.9)
        self.add(run_id, 5, 3.3)
        self.add(run_id, 10, 2.9)
        self.assertEqual(len(self.notifications(run_id)), 1)

    def test_stable_return_allows_a_new_range_episode(self) -> None:
        run_id = "new-high-episode"
        self.add(run_id, 0, 10.2)
        self.add(run_id, 5, 9.5)
        self.add(run_id, 10, 9.5)
        self.add(run_id, 15, 9.5)
        self.add(run_id, 20, 10.2)

        notifications = self.notifications(run_id)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(
            [event.phase.value for event in self.processor.list_events(run_id)
             if event.event_type == "demo_above_range"],
            ["started", "ended", "started"],
        )

    def test_gaps_and_out_of_order_timestamps_do_not_create_trend_alerts(self) -> None:
        run_id = "ordering-and-gaps"
        self.add(run_id, 0, 8.0)
        self.add(run_id, 15, 8.6)
        self.add(run_id, 30, 9.2)
        self.add(run_id, 25, 10.0, source_index=3)

        self.assertEqual(self.notifications(run_id), [])
        self.assertEqual(
            len(
                [
                    event
                    for event in self.processor.list_events(run_id)
                    if event.origin == "demonstration_notification_policy"
                ]
            ),
            0,
        )

    def test_ack_and_policy_latches_survive_processor_restart(self) -> None:
        run_id = "policy-restart"
        self.add(run_id, 0, 11.8)
        acknowledged = self.acknowledge_only_notification(run_id)
        self.add(run_id, 5, 11.3)
        self.add(run_id, 10, 10.7)
        self.add(run_id, 15, 11.2)
        self.add(run_id, 20, 12.0)

        self.database.dispose()
        self.database = Database(self.database.url)
        self.processor = ReadingProcessor(database=self.database)

        self.assertEqual(
            self.notifications(run_id)[0].status,
            NotificationStatus.READ,
        )
        self.add(run_id, 25, 12.5)
        self.add(run_id, 30, 13.1)
        notifications = self.notifications(run_id)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0].id, acknowledged.id)

    def test_runs_have_independent_episode_and_notification_state(self) -> None:
        self.add("run-a", 0, 11.8)
        self.add("run-b", 0, 11.8)
        self.acknowledge_only_notification("run-a")

        self.add("run-a", 5, 11.3)
        self.add("run-a", 10, 10.7)
        self.add("run-a", 15, 11.2)
        self.add("run-a", 20, 12.0)
        self.add("run-b", 5, 11.9)

        self.assertEqual(len(self.notifications("run-a")), 2)
        self.assertEqual(len(self.notifications("run-b")), 1)

    def test_duplicate_measurement_does_not_change_notification_state(self) -> None:
        run_id = "duplicate-policy-tick"
        self.add(run_id, 0, 11.8)
        self.assertFalse(
            self.add(run_id, 0, 11.8, source_index=0)
        )
        self.assertEqual(len(self.notifications(run_id)), 1)

    def test_fast_change_uses_three_recent_ordered_measurements(self) -> None:
        run_id = "three-point-rise"
        self.add(run_id, 0, 10.7)
        self.acknowledge_only_notification(run_id)
        self.add(run_id, 5, 11.2)
        self.add(run_id, 10, 12.0)

        notifications = self.notifications(run_id)
        rapid_rise_notifications = [
            item for item in notifications if "быстрый рост" in item.text
        ]
        self.assertEqual(len(rapid_rise_notifications), 1)


if __name__ == "__main__":
    unittest.main()
