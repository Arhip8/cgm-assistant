import csv
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
from pathlib import Path
from urllib.parse import urlencode

from app.services.simulation_controller import KRISTINA_DATA_DIR


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SimulationControlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_dir = tempfile.TemporaryDirectory(
            prefix="simulation-control-",
            dir=KRISTINA_DATA_DIR,
        )
        self.csv_path = Path(self.data_dir.name) / "readings.csv"
        self.write_readings(
            [
                ("2024-01-01 00:00:00", "5.5"),
                ("2024-01-01 01:00:00", "5.6"),
                ("2024-01-01 02:00:00", "5.7"),
            ]
        )
        self.database_dir = tempfile.TemporaryDirectory(
            prefix="cgm-simulation-db-"
        )
        self.database_path = Path(self.database_dir.name) / "test.sqlite3"
        self.start_server(timezone_name="UTC")

    def tearDown(self) -> None:
        self.stop_server()
        self.database_dir.cleanup()
        self.data_dir.cleanup()

    def write_readings(self, rows: list[tuple[str, str]]) -> None:
        with self.csv_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(("EventDateTime", "glucose_mmol_l"))
            writer.writerows(rows)

    def start_server(
        self,
        *,
        timezone_name: str | None,
        csv_path: Path | None = None,
    ) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        environment = os.environ.copy()
        environment["CGM_TESTING"] = "1"
        environment["CGM_DATABASE_URL"] = f"sqlite:///{self.database_path}"
        environment["CGM_SIMULATOR_CSV"] = str(csv_path or self.csv_path)
        if timezone_name is None:
            environment.pop("CGM_SIMULATOR_TIMEZONE", None)
        else:
            environment["CGM_SIMULATOR_TIMEZONE"] = timezone_name
        self.server = subprocess.Popen(
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
                str(self.port),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError("Uvicorn exited before the test API was ready")
            try:
                self.get("/health")
                return
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        self.stop_server()
        raise RuntimeError("Timed out waiting for the test API")

    def stop_server(self) -> None:
        server = getattr(self, "server", None)
        if server is not None and server.poll() is None:
            server.terminate()
            server.wait(timeout=15)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def get(self, path: str) -> tuple[int, object]:
        return self.request(path)

    def wait_for_status(
        self,
        expected_status: str,
        *,
        processed_at_least: int = 0,
        timeout: float = 10,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, result = self.get("/api/v1/simulation/status")
            assert isinstance(result, dict)
            if (
                result["status"] == expected_status
                and result["processed_readings"] >= processed_at_least
            ):
                return result
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {expected_status} at {processed_at_least}")

    def test_pause_resume_stop_and_other_routes_remain_responsive(self) -> None:
        status, started = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3600},
        )
        self.assertEqual(status, 200)
        self.assertEqual(started["status"], "running")
        self.assertEqual(started["total_readings"], 3)
        run_id = started["run_id"]

        self.wait_for_status("running", processed_at_least=1)
        status, paused = self.request("/api/v1/simulation/pause", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(paused["status"], "paused")
        processed = paused["processed_readings"]

        duplicate_status, duplicate = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 10},
        )
        self.assertEqual(duplicate_status, 409)
        self.assertIn("уже запущена", duplicate["detail"])
        self.assertEqual(self.get("/health")[0], 200)
        self.assertEqual(
            self.get(
                f"/api/v1/readings?{urlencode({'run_id': run_id})}"
            )[0],
            200,
        )

        resumed_status, resumed = self.request(
            "/api/v1/simulation/resume",
            method="POST",
        )
        self.assertEqual(resumed_status, 200)
        self.assertEqual(resumed["run_id"], run_id)
        self.assertEqual(resumed["status"], "running")
        stopped_status, stopped = self.request(
            "/api/v1/simulation/stop",
            method="POST",
        )
        self.assertEqual(stopped_status, 200)
        self.assertEqual(stopped["status"], "stopped")
        self.assertGreaterEqual(stopped["processed_readings"], processed)
        self.assertEqual(
            self.get(
                f"/api/v1/readings?{urlencode({'run_id': run_id})}"
            )[1].__len__(),
            stopped["processed_readings"],
        )

    def test_start_after_stop_creates_new_run_without_mixing_history(self) -> None:
        self.write_readings(
            [
                ("2024-01-01 00:00:00", "10.2"),
                ("2024-01-01 00:05:00", "10.3"),
                ("2024-01-01 00:10:00", "9.5"),
            ]
        )
        first_status, first = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 1},
        )
        self.assertEqual(first_status, 200)
        first_run_id = first["run_id"]
        self.assertEqual(first["current_source_index"], 0)
        self.wait_for_status("running", processed_at_least=1)

        stopped_status, stopped = self.request(
            "/api/v1/simulation/stop",
            method="POST",
        )
        self.assertEqual(stopped_status, 200)
        self.assertEqual(stopped["status"], "stopped")
        _, first_readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': first_run_id})}"
        )
        _, first_events = self.get(
            f"/api/v1/events?{urlencode({'run_id': first_run_id})}"
        )
        _, first_notifications = self.get(
            f"/api/v1/notifications?{urlencode({'run_id': first_run_id})}"
        )
        self.assertEqual([reading["source_index"] for reading in first_readings], [0])
        self.assertTrue(first_events)
        self.assertTrue(first_notifications)

        second_status, second = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 1},
        )
        self.assertEqual(second_status, 200)
        second_run_id = second["run_id"]
        self.assertNotEqual(second_run_id, first_run_id)
        self.assertEqual(second["current_source_index"], 0)
        self.wait_for_status("running", processed_at_least=1)

        busy_status, busy = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 1},
        )
        self.assertEqual(busy_status, 409)
        self.assertIn("уже запущена", busy["detail"])

        _, second_readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': second_run_id})}"
        )
        _, second_events = self.get(
            f"/api/v1/events?{urlencode({'run_id': second_run_id})}"
        )
        _, second_notifications = self.get(
            f"/api/v1/notifications?{urlencode({'run_id': second_run_id})}"
        )
        self.assertEqual([reading["source_index"] for reading in second_readings], [0])
        self.assertTrue(second_events)
        self.assertTrue(second_notifications)

        _, preserved_readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': first_run_id})}"
        )
        _, preserved_events = self.get(
            f"/api/v1/events?{urlencode({'run_id': first_run_id})}"
        )
        _, preserved_notifications = self.get(
            f"/api/v1/notifications?{urlencode({'run_id': first_run_id})}"
        )
        self.assertEqual(preserved_readings, first_readings)
        self.assertEqual(preserved_events, first_events)
        self.assertEqual(preserved_notifications, first_notifications)

        history_status, history = self.get("/api/v1/simulation/runs?limit=2")
        self.assertEqual(history_status, 200)
        self.assertEqual([item["run_id"] for item in history], [second_run_id, first_run_id])
        self.assertEqual(
            self.get("/api/v1/simulation/runs?limit=101")[0],
            422,
        )

    def test_completed_simulation_can_start_a_new_run(self) -> None:
        self.write_readings(
            [
                ("2024-01-01 00:00:00", "5.5"),
                ("2024-01-01 00:01:00", "5.6"),
            ]
        )
        _, first = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3600},
        )
        completed = self.wait_for_status(
            "completed",
            processed_at_least=2,
            timeout=10,
        )
        self.assertEqual(completed["run_id"], first["run_id"])

        restarted_status, restarted = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3600},
        )
        self.assertEqual(restarted_status, 200)
        self.assertNotEqual(restarted["run_id"], first["run_id"])
        self.assertEqual(restarted["current_source_index"], 0)
        self.wait_for_status("running", processed_at_least=1)

        _, old_readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': first['run_id']})}"
        )
        self.assertEqual([item["source_index"] for item in old_readings], [0, 1])

    def test_stop_while_paused_quiesces_worker_before_restart(self) -> None:
        _, first = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 1},
        )
        self.wait_for_status("running", processed_at_least=1)
        paused_status, paused = self.request(
            "/api/v1/simulation/pause",
            method="POST",
        )
        self.assertEqual(paused_status, 200)
        self.assertEqual(paused["status"], "paused")

        busy_status, busy = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 1},
        )
        self.assertEqual(busy_status, 409)
        self.assertIn("приостановлена", busy["detail"])

        stopped_status, stopped = self.request(
            "/api/v1/simulation/stop",
            method="POST",
        )
        self.assertEqual(stopped_status, 200)
        self.assertEqual(stopped["status"], "stopped")

        restarted_status, restarted = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 1},
        )
        self.assertEqual(restarted_status, 200)
        self.assertNotEqual(restarted["run_id"], first["run_id"])
        self.assertEqual(restarted["current_source_index"], 0)
        self.wait_for_status("running", processed_at_least=1)

        _, first_readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': first['run_id']})}"
        )
        self.assertEqual([item["source_index"] for item in first_readings], [0])

    def test_restart_recovers_saved_index_and_completes_without_duplicates(self) -> None:
        self.write_readings(
            [
                ("2024-01-01 00:00:00", "10.2"),
                ("2024-01-01 00:05:00", "10.3"),
                ("2024-01-01 00:10:00", "9.5"),
            ]
        )
        _, started = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 300},
        )
        run_id = started["run_id"]
        self.wait_for_status("running", processed_at_least=1)
        before_restart = self.wait_for_status("running", processed_at_least=1)
        saved_index = before_restart["current_source_index"]
        self.assertGreaterEqual(saved_index, 1)

        self.stop_server()
        self.start_server(timezone_name="UTC")
        recovered_status, recovered = self.get("/api/v1/simulation/status")
        self.assertEqual(recovered_status, 200)
        self.assertEqual(recovered["status"], "stopped")
        self.assertEqual(recovered["run_id"], run_id)
        self.assertEqual(recovered["current_source_index"], saved_index)

        resumed_status, resumed = self.request(
            "/api/v1/simulation/resume",
            method="POST",
        )
        self.assertEqual(resumed_status, 200)
        self.assertEqual(resumed["run_id"], run_id)
        completed = self.wait_for_status(
            "completed",
            processed_at_least=3,
            timeout=10,
        )
        self.assertEqual(completed["current_source_index"], 3)
        _, readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': run_id})}"
        )
        self.assertEqual([item["source_index"] for item in readings], [0, 1, 2])
        _, events = self.get(
            f"/api/v1/events?{urlencode({'run_id': run_id})}"
        )
        self.assertEqual([event["phase"] for event in events], ["started", "ended"])
        self.assertEqual(events[0]["event_id"], events[1]["event_id"])
        _, notifications = self.get(
            f"/api/v1/notifications?{urlencode({'run_id': run_id})}"
        )
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["event_id"], events[0]["event_id"])

        self.stop_server()
        self.write_readings(
            [
                ("2024-01-01 00:00:00", "6.5"),
                ("2024-01-01 01:00:00", "6.6"),
                ("2024-01-01 02:00:00", "6.7"),
            ]
        )
        self.start_server(timezone_name="UTC")
        _, completed_after_restart = self.get("/api/v1/simulation/status")
        self.assertEqual(completed_after_restart["status"], "completed")
        self.assertEqual(completed_after_restart["run_id"], run_id)
        self.assertEqual(completed_after_restart["processed_readings"], 3)

    def test_resume_rejects_changed_csv_after_restart(self) -> None:
        _, started = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3600},
        )
        run_id = started["run_id"]
        self.wait_for_status("running", processed_at_least=1)

        self.stop_server()
        self.write_readings(
            [
                ("2024-01-01 00:00:00", "6.5"),
                ("2024-01-01 01:00:00", "6.6"),
                ("2024-01-01 02:00:00", "6.7"),
            ]
        )
        self.start_server(timezone_name="UTC")

        _, recovered = self.get("/api/v1/simulation/status")
        self.assertEqual(recovered["status"], "error")
        self.assertIn("безопасное продолжение невозможно", recovered["error"])
        resume_status, _ = self.request(
            "/api/v1/simulation/resume",
            method="POST",
        )
        self.assertEqual(resume_status, 400)
        _, readings = self.get(
            f"/api/v1/readings?{urlencode({'run_id': run_id})}"
        )
        self.assertEqual([item["source_index"] for item in readings], [0])

    def test_invalid_speed_and_missing_timezone_are_rejected(self) -> None:
        status, _ = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3601},
        )
        self.assertEqual(status, 422)

        extra_field_status, _ = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={
                "speed_multiplier": 3600,
                "csv_path": str(self.csv_path),
            },
        )
        self.assertEqual(extra_field_status, 422)

        outside_csv = Path(self.database_dir.name) / "outside.csv"
        with outside_csv.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(("EventDateTime", "glucose_mmol_l"))
            writer.writerow(("2024-01-01 00:00:00", "5.5"))
        self.stop_server()
        self.start_server(timezone_name="UTC", csv_path=outside_csv)
        outside_status, outside_result = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3600},
        )
        self.assertEqual(outside_status, 400)
        self.assertIn("data/kristina", outside_result["detail"])

        self.stop_server()
        self.start_server(timezone_name=None)
        status, result = self.request(
            "/api/v1/simulation/start",
            method="POST",
            body={"speed_multiplier": 3600},
        )
        self.assertEqual(status, 400)
        self.assertIn("CGM_SIMULATOR_TIMEZONE", result["detail"])


if __name__ == "__main__":
    unittest.main()
