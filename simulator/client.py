from __future__ import annotations

import argparse
import csv
import json
import math
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1/readings"


class SimulatorError(Exception):
    """Base error for simulator playback failures."""


class SimulatorHTTPError(SimulatorError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Unexpected API response {status_code}: {message}")


class SimulatorConflictError(SimulatorHTTPError):
    def __init__(self, message: str) -> None:
        super().__init__(409, message or "Reading conflicts with a saved source_index")


class SimulatorNetworkError(SimulatorError):
    """The reading was not confirmed by the API due to a network failure."""


@dataclass(frozen=True)
class SimulatorReading:
    source_index: int
    measured_at: datetime
    glucose_mmol_l: float


def _parse_timestamp(value: str | None, timezone_name: str | None, row_number: int) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"CSV row {row_number}: EventDateTime is empty")
    try:
        timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"CSV row {row_number}: invalid EventDateTime {value!r}"
        ) from exc

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        if not timezone_name:
            raise ValueError(
                "CSV contains timezone-naive timestamps; pass an explicit "
                "timezone_name (for example 'Europe/Moscow')"
            )
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
        timestamp = timestamp.replace(tzinfo=timezone)
    return timestamp


def _glucose_value(row: dict[str, str], row_number: int) -> float:
    raw_value = (row.get("glucose_mmol_l") or "").strip()
    if raw_value:
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"CSV row {row_number}: glucose_mmol_l must be numeric"
            ) from exc
    else:
        raw_mg_dl = (row.get("CGM") or "").strip()
        try:
            value = float(raw_mg_dl) / 18.0
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"CSV row {row_number}: glucose_mmol_l and CGM are unavailable"
            ) from exc

    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"CSV row {row_number}: glucose must be a finite positive number"
        )
    return value


def load_readings(
    csv_path: str | Path,
    *,
    timezone_name: str | None = None,
) -> list[SimulatorReading]:
    """Load, validate, sort, and de-duplicate historical CSV measurements."""
    path = Path(csv_path)
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        required = {"EventDateTime"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        if not ({"glucose_mmol_l", "CGM"} & set(reader.fieldnames or ())):
            raise ValueError("CSV must contain glucose_mmol_l or CGM")

        parsed: list[tuple[int, datetime, float]] = []
        for row_number, row in enumerate(reader, start=2):
            timestamp = _parse_timestamp(
                row.get("EventDateTime", ""),
                timezone_name,
                row_number,
            )
            value = _glucose_value(row, row_number)
            parsed.append((row_number, timestamp, value))

    parsed.sort(key=lambda item: (item[1].astimezone(timezone.utc), item[0]))
    unique: list[tuple[datetime, float]] = []
    seen_timestamps: set[datetime] = set()
    for _, timestamp, value in parsed:
        key = timestamp.astimezone(timezone.utc)
        if key in seen_timestamps:
            continue
        seen_timestamps.add(key)
        unique.append((timestamp, value))

    return [
        SimulatorReading(
            source_index=index,
            measured_at=timestamp,
            glucose_mmol_l=value,
        )
        for index, (timestamp, value) in enumerate(unique)
    ]


class GlucoseSimulator:
    """Replay historical glucose readings through the CGM Assistant readings API."""

    def __init__(
        self,
        csv_path: str | Path,
        *,
        run_id: str | None = None,
        api_url: str = DEFAULT_API_URL,
        timezone_name: str | None = None,
        start_index: int = 0,
        request_timeout: float = 10.0,
    ) -> None:
        if not api_url:
            raise ValueError("api_url must not be empty")
        if request_timeout <= 0 or not math.isfinite(request_timeout):
            raise ValueError("request_timeout must be finite and positive")
        self.readings = load_readings(
            csv_path,
            timezone_name=timezone_name,
        )
        if (
            isinstance(start_index, bool)
            or not isinstance(start_index, int)
            or start_index < 0
            or start_index > len(self.readings)
        ):
            raise ValueError("start_index must be within the CSV measurement range")
        self.api_url = api_url
        self.run_id = (
            run_id if run_id is not None else f"run_{uuid.uuid4().hex[:8]}"
        )
        if not self.run_id.strip():
            raise ValueError("run_id must not be blank")
        self.current_index = start_index
        self.success_count = 0
        self.duplicate_count = 0
        self.error_count = 0
        self.request_timeout = request_timeout
        self._condition = Condition()
        self._paused = False
        self._stop_requested = False
        self._run_lock = Lock()

    def prepare_json_payload(self, reading: SimulatorReading) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "source_index": reading.source_index,
            "measured_at": reading.measured_at.isoformat(),
            "glucose_mmol_l": reading.glucose_mmol_l,
        }

    def send_to_api(self, payload: dict[str, object]) -> int:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.request_timeout,
            ) as response:
                status_code = response.status
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            status_code = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SimulatorNetworkError(
                f"Could not send source_index {payload['source_index']}: {exc}"
            ) from exc

        if status_code == 409:
            raise SimulatorConflictError(response_body)
        if status_code not in (200, 201):
            raise SimulatorHTTPError(status_code, response_body)
        return status_code

    def pause(self) -> None:
        with self._condition:
            self._paused = True
            self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._stop_requested = False
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._paused = False
            self._condition.notify_all()

    def _wait_until_ready(self, delay_seconds: float) -> bool:
        remaining = max(delay_seconds, 0.0)
        with self._condition:
            while True:
                while self._paused and not self._stop_requested:
                    self._condition.wait()
                if self._stop_requested:
                    return False
                if remaining <= 0:
                    return True
                started = time.monotonic()
                self._condition.wait(timeout=remaining)
                remaining -= max(time.monotonic() - started, 0.0)

    def _begin_request(self) -> bool:
        with self._condition:
            while self._paused and not self._stop_requested:
                self._condition.wait()
            return not self._stop_requested

    def run_simulation(
        self,
        *,
        speed_multiplier: float = 60.0,
        max_records: int | None = None,
        log_every: int = 10,
    ) -> dict[str, int | bool]:
        if isinstance(speed_multiplier, bool) or not math.isfinite(speed_multiplier):
            raise ValueError("speed_multiplier must be finite and positive")
        if speed_multiplier <= 0:
            raise ValueError("speed_multiplier must be finite and positive")
        if max_records is not None and (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or max_records < 0
        ):
            raise ValueError("max_records must be non-negative")
        if isinstance(log_every, bool) or not isinstance(log_every, int) or log_every <= 0:
            raise ValueError("log_every must be positive")
        if not self._run_lock.acquire(blocking=False):
            raise SimulatorError("This simulator is already running")

        processed = 0
        stopped = False
        try:
            with self._condition:
                self._stop_requested = False

            while self.current_index < len(self.readings):
                if max_records is not None and processed >= max_records:
                    break

                if self.current_index > 0:
                    previous = self.readings[self.current_index - 1]
                    current = self.readings[self.current_index]
                    interval_seconds = (
                        current.measured_at.astimezone(timezone.utc)
                        - previous.measured_at.astimezone(timezone.utc)
                    ).total_seconds()
                    if not self._wait_until_ready(interval_seconds / speed_multiplier):
                        stopped = True
                        break
                elif not self._wait_until_ready(0):
                    stopped = True
                    break

                reading = self.readings[self.current_index]
                payload = self.prepare_json_payload(reading)
                if not self._begin_request():
                    stopped = True
                    break
                try:
                    status_code = self.send_to_api(payload)
                except SimulatorError:
                    self.error_count += 1
                    raise

                self.success_count += 1
                if status_code == 200:
                    self.duplicate_count += 1
                self.current_index += 1
                processed += 1

                if (processed - 1) % log_every == 0:
                    print(
                        f"[HTTP {status_code}] {self.current_index}/{len(self.readings)} "
                        f"{payload['measured_at']} | {payload['glucose_mmol_l']} ммоль/л"
                    )

            return {
                "success": self.success_count,
                "duplicates": self.duplicate_count,
                "errors": self.error_count,
                "processed_this_run": processed,
                "source_index": (
                    self.readings[self.current_index].source_index
                    if self.current_index < len(self.readings)
                    else (
                        self.readings[-1].source_index + 1
                        if self.readings
                        else 0
                    )
                ),
                "stopped": stopped,
            }
        finally:
            self._run_lock.release()


class GlucoseSimulatorLogOnly(GlucoseSimulator):
    """Offline dry-run variant; it does not contact the backend."""

    def send_to_api(self, payload: dict[str, object]) -> int:
        print(f"[LOG ONLY] {json.dumps(payload, ensure_ascii=False)}")
        return 201


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay historical CGM readings to the CGM Assistant API."
    )
    parser.add_argument("--csv", required=True, help="Path to source CSV")
    parser.add_argument("--timezone", help="Explicit timezone for naive CSV timestamps")
    parser.add_argument("--run-id", help="Run identifier (generated when omitted)")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--speed-multiplier", type=float, default=60.0)
    parser.add_argument("--max-records", type=int)
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Resume at this CSV row index without changing its source_index",
    )
    args = parser.parse_args()

    simulator = GlucoseSimulator(
        args.csv,
        timezone_name=args.timezone,
        run_id=args.run_id,
        api_url=args.api_url,
        start_index=args.start_index,
    )
    print(f"run_id={simulator.run_id}; readings={len(simulator.readings)}")
    try:
        result = simulator.run_simulation(
            speed_multiplier=args.speed_multiplier,
            max_records=args.max_records,
        )
    except KeyboardInterrupt:
        simulator.stop()
        print(
            f"Playback interrupted; resume this simulator instance at "
            f"start_index={simulator.current_index} with run_id={simulator.run_id}"
        )
        return
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
