import unittest

from app.api.schemas.analysis import AnalysisEvent, EventPhase
from app.api.schemas.notification import NotificationStatus
from app.services.notification_service import InMemoryNotificationService


def make_event(
    *,
    event_id: str = "event-001",
    run_id: str = "demo-001",
    event_type: str = "demo_above_range",
    phase: EventPhase = EventPhase.STARTED,
) -> AnalysisEvent:
    return AnalysisEvent(
        event_id=event_id,
        run_id=run_id,
        event_type=event_type,
        phase=phase,
        origin="demonstration_algorithm",
        priority_hint="info",
        started_at="2026-11-12T10:00:00+03:00",
        ended_at=None,
        measured_at="2026-11-12T10:00:00+03:00",
        glucose_mmol_l=10.2,
        details={},
    )


class NotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = InMemoryNotificationService()

    def test_creates_notification_for_significant_event_start(self) -> None:
        created = self.service.process_events([make_event()])

        self.assertEqual(len(created), 1)
        notification = created[0]
        self.assertEqual(notification.run_id, "demo-001")
        self.assertEqual(notification.event_id, "event-001")
        self.assertIn("демонстрационное событие", notification.text)
        self.assertEqual(notification.status, NotificationStatus.UNREAD)
        self.assertIsNone(notification.acknowledged_at)

    def test_deduplicates_same_event_id(self) -> None:
        event = make_event()

        first = self.service.process_events([event])
        repeated = self.service.process_events([event])

        self.assertEqual(len(first), 1)
        self.assertEqual(repeated, [])
        self.assertEqual(len(self.service.list()), 1)

    def test_event_continuation_does_not_create_notification(self) -> None:
        notifications = self.service.process_events(
            [make_event(phase=EventPhase.ENDED)]
        )

        self.assertEqual(notifications, [])
        self.assertEqual(self.service.list(), [])

    def test_observed_gap_and_interruption_remain_without_notification(self) -> None:
        events = [
            make_event(
                event_id="gap",
                event_type="data_gap",
                phase=EventPhase.OBSERVED,
            ),
            make_event(event_id="interrupted", phase=EventPhase.INTERRUPTED),
        ]

        self.assertEqual(self.service.process_events(events), [])
        self.assertEqual(self.service.list(), [])

    def test_independent_events_create_independent_notifications(self) -> None:
        events = [
            make_event(event_id="range", event_type="demo_above_range"),
            make_event(event_id="trend", event_type="demo_rapid_rise"),
        ]

        notifications = self.service.process_events(events)

        self.assertEqual(len(notifications), 2)
        self.assertEqual(
            {notification.event_id for notification in notifications},
            {"range", "trend"},
        )
        self.assertEqual(len({notification.id for notification in notifications}), 2)

    def test_unknown_event_type_is_not_notified(self) -> None:
        self.assertEqual(
            self.service.process_events(
                [make_event(event_type="unrecognized_event")]
            ),
            [],
        )

    def test_acknowledgement_is_idempotent(self) -> None:
        notification = self.service.process_events([make_event()])[0]

        acknowledged = self.service.acknowledge(notification.id)
        acknowledged_again = self.service.acknowledge(notification.id)

        self.assertEqual(acknowledged.status, NotificationStatus.READ)
        self.assertIsNotNone(acknowledged.acknowledged_at)
        self.assertEqual(acknowledged_again.acknowledged_at, acknowledged.acknowledged_at)

    def test_notifications_are_filtered_by_run_id(self) -> None:
        self.service.process_events(
            [
                make_event(event_id="run-a", run_id="run-a"),
                make_event(event_id="run-b", run_id="run-b"),
            ]
        )

        self.assertEqual(
            [item.run_id for item in self.service.list(run_id="run-a")],
            ["run-a"],
        )
        self.assertEqual(
            [item.run_id for item in self.service.list(run_id="run-b")],
            ["run-b"],
        )


if __name__ == "__main__":
    unittest.main()
