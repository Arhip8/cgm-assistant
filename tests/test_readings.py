import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlencode


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReadingsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="cgm-api-tests-")
        cls.database_path = Path(cls.temp_dir.name) / "api-tests.sqlite3"

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]

        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment["CGM_TESTING"] = "1"
        environment["CGM_DATABASE_URL"] = f"sqlite:///{cls.database_path}"
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(PROJECT_ROOT / "backend"),
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                cls.temp_dir.cleanup()
                raise RuntimeError("Uvicorn exited before the test server was ready")
            try:
                with urllib.request.urlopen(f"{cls.base_url}/health", timeout=1):
                    return
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)

        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.temp_dir.cleanup()
        raise RuntimeError("Timed out waiting for the test server to start")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.server.poll() is None:
            cls.server.terminate()
            cls.server.wait(timeout=5)
        cls.temp_dir.cleanup()

    def post_reading(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/readings",
            data=json.dumps(payload, allow_nan=True)
            .replace("Infinity", "1e9999")
            .encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def get_readings(self, run_id: str) -> tuple[int, list[dict[str, object]]]:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/readings?{urlencode({'run_id': run_id})}"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def get_events(self, run_id: str) -> tuple[int, list[dict[str, object]]]:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/events?{urlencode({'run_id': run_id})}"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def get_notifications(
        self, run_id: str
    ) -> tuple[int, list[dict[str, object]]]:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/notifications?{urlencode({'run_id': run_id})}"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def acknowledge_notification(
        self, notification_id: str
    ) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/notifications/{notification_id}/acknowledge",
            data=b"",
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def make_reading(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "run_id": f"test-{uuid.uuid4()}",
            "source_index": 17,
            "measured_at": "2026-11-12T12:05:00+03:00",
            "glucose_mmol_l": 8.6,
        }
        payload.update(overrides)
        return payload

    def test_accepts_reading(self) -> None:
        payload = self.make_reading()

        status_code, body = self.post_reading(payload)

        self.assertEqual(status_code, 201)
        self.assertEqual(body, payload)
        self.assertTrue(self.database_path.is_file())
        self.assertNotEqual(
            self.database_path.resolve(),
            (PROJECT_ROOT / "data" / "cgm_assistant.sqlite3").resolve(),
        )

    def test_returns_reading_history_filtered_by_run_id(self) -> None:
        payload = self.make_reading()
        self.post_reading(payload)

        status_code, history = self.get_readings(str(payload["run_id"]))

        self.assertEqual(status_code, 200)
        self.assertEqual(history, [payload])

    def test_rejects_invalid_readings(self) -> None:
        invalid_payloads = [
            self.make_reading(run_id=""),
            self.make_reading(run_id="   "),
            self.make_reading(source_index=-1),
            self.make_reading(source_index=1.5),
            self.make_reading(
                measured_at="2026-11-12T12:05:00"
            ),
            self.make_reading(measured_at=1794488700),
            self.make_reading(glucose_mmol_l=0),
            self.make_reading(glucose_mmol_l=float("inf")),
            self.make_reading(unexpected="field"),
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                status_code, _ = self.post_reading(payload)
                self.assertEqual(status_code, 422)

    def test_identical_repeated_reading_is_idempotent(self) -> None:
        payload = self.make_reading()

        first_status, first_body = self.post_reading(payload)
        second_status, second_body = self.post_reading(payload)

        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 200)
        self.assertEqual(first_body, second_body)
        _, history = self.get_readings(str(payload["run_id"]))
        self.assertEqual(history, [payload])

    def test_conflicting_duplicate_identifier_returns_409(self) -> None:
        payload = self.make_reading()
        conflicting_payload = {**payload, "glucose_mmol_l": 9.1}
        self.post_reading(payload)

        status_code, body = self.post_reading(conflicting_payload)

        self.assertEqual(status_code, 409)
        self.assertIn("already exists", str(body["detail"]))

    def test_detects_new_event_and_preserves_event_fields(self) -> None:
        payload = self.make_reading(
            run_id=f"event-start-{uuid.uuid4()}",
            source_index=1,
            glucose_mmol_l=10.2,
        )

        status_code, _ = self.post_reading(payload)
        event_status, events = self.get_events(str(payload["run_id"]))

        self.assertEqual(status_code, 201)
        self.assertEqual(event_status, 200)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "demo_above_range")
        self.assertEqual(event["phase"], "started")
        self.assertTrue(event["event_id"])
        self.assertEqual(event["started_at"], payload["measured_at"])
        self.assertIsNone(event["ended_at"])
        self.assertEqual(event["measured_at"], payload["measured_at"])
        self.assertNotIn("create_information_notification", event)

    def test_identical_retry_does_not_add_duplicate_events(self) -> None:
        payload = self.make_reading(
            run_id=f"event-retry-{uuid.uuid4()}",
            source_index=1,
            glucose_mmol_l=10.2,
        )
        first_status, _ = self.post_reading(payload)
        retry_status, _ = self.post_reading(payload)
        _, events = self.get_events(str(payload["run_id"]))

        self.assertEqual(first_status, 201)
        self.assertEqual(retry_status, 200)
        self.assertEqual(len(events), 1)

    def test_event_completion_keeps_episode_id(self) -> None:
        run_id = f"event-end-{uuid.uuid4()}"
        start = self.make_reading(
            run_id=run_id,
            source_index=1,
            measured_at="2026-11-12T10:00:00+03:00",
            glucose_mmol_l=10.2,
        )
        end = self.make_reading(
            run_id=run_id,
            source_index=2,
            measured_at="2026-11-12T10:05:00+03:00",
            glucose_mmol_l=9.5,
        )
        self.post_reading(start)
        self.post_reading(end)

        _, events = self.get_events(run_id)

        self.assertEqual([event["phase"] for event in events], ["started", "ended"])
        self.assertEqual(events[0]["event_id"], events[1]["event_id"])
        self.assertEqual(events[1]["started_at"], start["measured_at"])
        self.assertEqual(events[1]["ended_at"], end["measured_at"])

    def test_preserves_multiple_events_from_one_reading(self) -> None:
        run_id = f"multi-event-{uuid.uuid4()}"
        for index, (minute, value) in enumerate(((0, 8.0), (5, 9.3), (10, 10.6))):
            payload = self.make_reading(
                run_id=run_id,
                source_index=index,
                measured_at=f"2026-11-12T10:{minute:02}:00+03:00",
                glucose_mmol_l=value,
            )
            status, _ = self.post_reading(payload)
            self.assertEqual(status, 201)

        _, events = self.get_events(run_id)
        final_measurement_events = [
            event for event in events
            if event["measured_at"] == "2026-11-12T10:10:00+03:00"
        ]
        self.assertEqual(
            {event["event_type"] for event in final_measurement_events},
            {"demo_above_range", "demo_rapid_rise"},
        )

    def test_events_are_filtered_by_run_id(self) -> None:
        first_run = f"isolated-a-{uuid.uuid4()}"
        second_run = f"isolated-b-{uuid.uuid4()}"
        self.post_reading(self.make_reading(run_id=first_run, glucose_mmol_l=10.2))
        self.post_reading(self.make_reading(run_id=second_run, glucose_mmol_l=3.8))

        _, first_events = self.get_events(first_run)
        _, second_events = self.get_events(second_run)

        self.assertEqual({event["run_id"] for event in first_events}, {first_run})
        self.assertEqual({event["run_id"] for event in second_events}, {second_run})
        self.assertNotEqual(first_events[0]["event_type"], second_events[0]["event_type"])

    def test_invalid_request_does_not_poison_later_valid_reading(self) -> None:
        run_id = f"validation-recovery-{uuid.uuid4()}"
        invalid = self.make_reading(
            run_id=run_id,
            source_index=1,
            glucose_mmol_l=0,
        )
        valid = {**invalid, "glucose_mmol_l": 10.2}

        invalid_status, _ = self.post_reading(invalid)
        valid_status, _ = self.post_reading(valid)
        _, events = self.get_events(run_id)

        self.assertEqual(invalid_status, 422)
        self.assertEqual(valid_status, 201)
        self.assertEqual(len(events), 1)

    def test_measurement_event_notification_and_acknowledgement_flow(self) -> None:
        run_id = f"notification-flow-{uuid.uuid4()}"
        payload = self.make_reading(
            run_id=run_id,
            source_index=1,
            glucose_mmol_l=10.2,
        )
        measurement_status, measurement_body = self.post_reading(payload)
        event_status, events = self.get_events(run_id)
        notification_status, notifications = self.get_notifications(run_id)

        self.assertEqual(measurement_status, 201)
        self.assertEqual(measurement_body, payload)
        self.assertEqual(event_status, 200)
        self.assertEqual(notification_status, 200)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["event_id"], events[0]["event_id"])
        self.assertEqual(notifications[0]["status"], "unread")
        self.assertIsNone(notifications[0]["acknowledged_at"])

        acknowledge_status, acknowledged = self.acknowledge_notification(
            str(notifications[0]["id"])
        )

        self.assertEqual(acknowledge_status, 200)
        self.assertEqual(acknowledged["status"], "read")
        self.assertIsNotNone(acknowledged["acknowledged_at"])
        _, refreshed = self.get_notifications(run_id)
        self.assertEqual(refreshed[0]["status"], "read")

    def test_duplicate_measurement_does_not_duplicate_notification(self) -> None:
        payload = self.make_reading(
            run_id=f"notification-retry-{uuid.uuid4()}",
            source_index=1,
            glucose_mmol_l=10.2,
        )
        first_status, _ = self.post_reading(payload)
        retry_status, _ = self.post_reading(payload)
        _, notifications = self.get_notifications(str(payload["run_id"]))

        self.assertEqual(first_status, 201)
        self.assertEqual(retry_status, 200)
        self.assertEqual(len(notifications), 1)


if __name__ == "__main__":
    unittest.main()
