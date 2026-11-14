import csv
import io
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from simulator import (
    GlucoseSimulator,
    GlucoseSimulatorLogOnly,
    SimulatorConflictError,
    SimulatorHTTPError,
    SimulatorNetworkError,
    load_readings,
)


class FakeGlucoseSimulator(GlucoseSimulator):
    def __init__(self, *args, responses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = list(responses or [])
        self.payloads = []

    def send_to_api(self, payload):
        self.payloads.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StopAfterFirstReadingSimulator(FakeGlucoseSimulator):
    def send_to_api(self, payload):
        response = super().send_to_api(payload)
        if len(self.payloads) == 1:
            self.stop()
        return response


class WaitBeforeRequestSimulator(FakeGlucoseSimulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.boundary_reached = Event()
        self.release_boundary = Event()
        self._blocked_once = False

    def _wait_until_ready(self, delay_seconds):
        ready = super()._wait_until_ready(delay_seconds)
        if ready and not self._blocked_once:
            self._blocked_once = True
            self.boundary_reached.set()
            self.release_boundary.wait(timeout=3)
        return ready


class FakeHTTPResponse(io.BytesIO):
    def __init__(self, status):
        super().__init__(b'{"ok": true}')
        self.status = status


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "readings.csv"
        self.write_rows(
            [
                {"EventDateTime": "2024-01-01 00:10:00", "glucose_mmol_l": "6.2"},
                {"EventDateTime": "2024-01-01 00:00:00", "glucose_mmol_l": "5.4"},
                {"EventDateTime": "2024-01-01 00:10:00", "glucose_mmol_l": "6.5"},
                {"EventDateTime": "2024-01-01 00:25:00", "glucose_mmol_l": "7.1"},
            ]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_rows(self, rows):
        with self.csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def make_simulator(self, responses=None, **kwargs):
        return FakeGlucoseSimulator(
            self.csv_path,
            run_id="simulator-test",
            timezone_name="Europe/Moscow",
            responses=responses,
            **kwargs,
        )

    def test_sorts_deduplicates_and_assigns_exact_api_payload(self):
        readings = load_readings(
            self.csv_path,
            timezone_name="Europe/Moscow",
        )
        self.assertEqual([reading.source_index for reading in readings], [0, 1, 2])
        self.assertEqual(
            [reading.measured_at.minute for reading in readings],
            [0, 10, 25],
        )
        self.assertEqual(
            readings[0].measured_at.isoformat(),
            "2024-01-01T00:00:00+03:00",
        )

        simulator = self.make_simulator(responses=[201])
        simulator.run_simulation(speed_multiplier=1_000_000, max_records=1)
        self.assertEqual(
            set(simulator.payloads[0]),
            {"run_id", "source_index", "measured_at", "glucose_mmol_l"},
        )
        self.assertEqual(simulator.payloads[0]["run_id"], "simulator-test")
        self.assertEqual(simulator.payloads[0]["source_index"], 0)
        self.assertEqual(simulator.payloads[0]["glucose_mmol_l"], 5.4)

    def test_timezone_is_mandatory_for_naive_csv_and_can_be_explicit(self):
        with self.assertRaisesRegex(ValueError, "explicit timezone_name"):
            load_readings(self.csv_path)
        with self.assertRaisesRegex(ValueError, "Unknown timezone"):
            load_readings(self.csv_path, timezone_name="Not/A_Zone")

    def test_timezone_aware_timestamps_are_preserved(self):
        self.write_rows(
            [{"EventDateTime": "2024-01-01T00:00:00+05:30", "glucose_mmol_l": "5.4"}]
        )
        reading = load_readings(self.csv_path)[0]
        self.assertEqual(reading.measured_at.isoformat(), "2024-01-01T00:00:00+05:30")

    def test_falls_back_to_cgm_mg_dl_without_using_derived_labels(self):
        self.write_rows(
            [
                {
                    "EventDateTime": "2024-01-01 00:00:00",
                    "CGM": "108",
                    "Anomaly_Type": "UNTRUSTED_LABEL",
                    "Action_Recommended": "DO NOT SEND",
                }
            ]
        )
        simulator = self.make_simulator(responses=[201])
        simulator.run_simulation(speed_multiplier=1_000_000)
        payload = simulator.payloads[0]
        self.assertEqual(payload["glucose_mmol_l"], 6)
        self.assertEqual(
            set(payload),
            {"run_id", "source_index", "measured_at", "glucose_mmol_l"},
        )

    def test_http_created_and_duplicate_responses_are_successful(self):
        simulator = self.make_simulator(responses=[201, 200])
        first = simulator.run_simulation(
            speed_multiplier=1_000_000,
            max_records=1,
        )
        second = simulator.run_simulation(
            speed_multiplier=1_000_000,
            max_records=1,
        )
        self.assertEqual(first["success"], 1)
        self.assertEqual(second["success"], 2)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(simulator.payloads[0]["source_index"], 0)
        self.assertEqual(simulator.payloads[1]["source_index"], 1)
        self.assertEqual(simulator.run_id, "simulator-test")

    def test_explicit_resume_index_keeps_original_source_index(self):
        simulator = self.make_simulator(
            responses=[201],
            start_index=1,
        )
        simulator.run_simulation(
            speed_multiplier=1_000_000,
            max_records=1,
        )
        self.assertEqual(simulator.payloads[0]["source_index"], 1)
        self.assertEqual(simulator.payloads[0]["measured_at"], "2024-01-01T00:10:00+03:00")

    def test_conflict_and_network_failures_do_not_advance_cursor(self):
        simulator = self.make_simulator(
            responses=[SimulatorConflictError("duplicate ID")]
        )
        with self.assertRaises(SimulatorConflictError):
            simulator.run_simulation(speed_multiplier=1_000_000)
        self.assertEqual(simulator.current_index, 0)
        self.assertEqual(simulator.error_count, 1)

        simulator.responses.append(SimulatorNetworkError("offline"))
        with self.assertRaises(SimulatorNetworkError):
            simulator.run_simulation(speed_multiplier=1_000_000)
        self.assertEqual(simulator.current_index, 0)
        self.assertEqual(simulator.error_count, 2)

        simulator.responses.append(201)
        simulator.run_simulation(speed_multiplier=1_000_000, max_records=1)
        self.assertEqual(simulator.current_index, 1)
        self.assertEqual(simulator.payloads[-1]["source_index"], 0)

    def test_unexpected_http_status_is_an_error_and_does_not_skip(self):
        simulator = self.make_simulator(
            responses=[SimulatorHTTPError(503, "unavailable")]
        )
        with self.assertRaises(SimulatorHTTPError):
            simulator.run_simulation(speed_multiplier=1_000_000)
        self.assertEqual(simulator.current_index, 0)

    def test_http_transport_maps_created_duplicate_conflict_and_network_errors(self):
        simulator = GlucoseSimulator(
            self.csv_path,
            run_id="simulator-test",
            timezone_name="Europe/Moscow",
        )
        payload = simulator.prepare_json_payload(simulator.readings[0])

        with patch(
            "simulator.client.urllib.request.urlopen",
            return_value=FakeHTTPResponse(201),
        ):
            self.assertEqual(simulator.send_to_api(payload), 201)
        with patch(
            "simulator.client.urllib.request.urlopen",
            return_value=FakeHTTPResponse(200),
        ):
            self.assertEqual(simulator.send_to_api(payload), 200)

        conflict = HTTPError(
            simulator.api_url,
            409,
            "Conflict",
            None,
            io.BytesIO(b"duplicate"),
        )
        with patch(
            "simulator.client.urllib.request.urlopen",
            side_effect=conflict,
        ):
            with self.assertRaises(SimulatorConflictError):
                simulator.send_to_api(payload)

        with patch(
            "simulator.client.urllib.request.urlopen",
            return_value=FakeHTTPResponse(503),
        ):
            with self.assertRaises(SimulatorHTTPError) as raised:
                simulator.send_to_api(payload)
        self.assertEqual(raised.exception.status_code, 503)

        with patch(
            "simulator.client.urllib.request.urlopen",
            side_effect=URLError("offline"),
        ):
            with self.assertRaises(SimulatorNetworkError):
                simulator.send_to_api(payload)

    def test_stop_resume_keeps_run_id_and_source_index(self):
        simulator = StopAfterFirstReadingSimulator(
            self.csv_path,
            run_id="simulator-test",
            timezone_name="Europe/Moscow",
            responses=[201, 201, 201],
        )
        simulator.pause()
        worker = Thread(
            target=simulator.run_simulation,
            kwargs={"speed_multiplier": 1_000_000},
        )
        worker.start()
        self.assertEqual(simulator.payloads, [])
        simulator.resume()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())

        self.assertEqual(simulator.current_index, 1)
        self.assertEqual(simulator.run_id, "simulator-test")
        resumed = simulator.run_simulation(
            speed_multiplier=1_000_000,
        )
        self.assertEqual(resumed["processed_this_run"], 2)
        self.assertEqual(
            [payload["source_index"] for payload in simulator.payloads],
            [0, 1, 2],
        )
        self.assertEqual(
            {payload["run_id"] for payload in simulator.payloads},
            {"simulator-test"},
        )

    def test_pause_between_wait_and_request_blocks_the_next_reading(self):
        simulator = WaitBeforeRequestSimulator(
            self.csv_path,
            run_id="pause-boundary",
            timezone_name="Europe/Moscow",
            responses=[201, 201, 201],
        )
        worker = Thread(
            target=simulator.run_simulation,
            kwargs={"speed_multiplier": 1_000_000},
        )
        worker.start()
        self.assertTrue(simulator.boundary_reached.wait(timeout=3))
        simulator.pause()
        simulator.release_boundary.set()
        time.sleep(0.05)
        self.assertEqual(simulator.payloads, [])

        simulator.resume()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(
            [payload["source_index"] for payload in simulator.payloads],
            [0, 1, 2],
        )

    def test_log_only_variant_does_not_send_requests(self):
        simulator = GlucoseSimulatorLogOnly(
            self.csv_path,
            run_id="dry-run",
            timezone_name="Europe/Moscow",
        )
        result = simulator.run_simulation(
            speed_multiplier=1_000_000,
            max_records=1,
        )
        self.assertEqual(result["success"], 1)
        self.assertEqual(simulator.current_index, 1)


if __name__ == "__main__":
    unittest.main()
