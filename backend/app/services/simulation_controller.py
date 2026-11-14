from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from threading import RLock, Thread
from typing import Any
from uuid import uuid4

from app.api.schemas.reading import Reading
from app.api.schemas.simulation import DEFAULT_SPEED_MULTIPLIER, MAX_SPEED_MULTIPLIER
from app.services.reading_processor import ReadingProcessor
from app.services.reading_store import ReadingConflictError
from simulator import GlucoseSimulator, SimulatorConflictError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
KRISTINA_DATA_DIR = (PROJECT_ROOT / "data" / "kristina").resolve()
DEFAULT_CSV_PATH = KRISTINA_DATA_DIR / "cleaned_glucose_data.csv"


class SimulationError(Exception):
    pass


class SimulationBusyError(SimulationError):
    pass


class SimulationStateError(SimulationError):
    pass


class SimulationConfigurationError(SimulationError):
    pass


class _PipelineSimulator(GlucoseSimulator):
    def __init__(self, processor: ReadingProcessor, *args: Any, **kwargs: Any) -> None:
        self._processor = processor
        super().__init__(*args, **kwargs)

    def send_to_api(self, payload: dict[str, object]) -> int:
        try:
            created = self._processor.add_and_analyze(Reading.model_validate(payload))
        except ReadingConflictError as exc:
            raise SimulatorConflictError(str(exc)) from exc
        return 201 if created else 200


class SimulationController:
    def __init__(
        self,
        *,
        processor: ReadingProcessor,
        csv_path: str | Path | None = None,
        timezone_name: str | None = None,
    ) -> None:
        configured_path = csv_path or os.environ.get(
            "CGM_SIMULATOR_CSV",
            str(DEFAULT_CSV_PATH),
        )
        self.csv_path = Path(configured_path).expanduser().resolve()
        self.timezone_name = (
            timezone_name
            if timezone_name is not None
            else os.environ.get("CGM_SIMULATOR_TIMEZONE")
        )
        self._processor = processor
        self._lock = RLock()
        self._thread: Thread | None = None
        self._simulator: _PipelineSimulator | None = None
        self._run_id: str | None = None
        self._status = "idle"
        self._total_readings = 0
        self._processed_readings = 0
        self._speed_multiplier = DEFAULT_SPEED_MULTIPLIER
        self._error: str | None = None

    def recover(self) -> None:
        recovered = self._processor.latest_server_simulation()
        if recovered is None:
            return
        run_id, stored_status, processed, speed_multiplier = recovered
        with self._lock:
            self._run_id = run_id
            self._processed_readings = processed
            self._speed_multiplier = speed_multiplier
            if stored_status == "completed":
                self._total_readings = processed
                self._status = "completed"
                return
            try:
                self._total_readings = len(
                    self._load_simulator(run_id, processed).readings
                )
            except (OSError, ValueError, SimulationConfigurationError) as exc:
                self._status = "error"
                self._error = f"Не удалось восстановить симуляцию: {exc}"
                self._processor.set_simulation_status(run_id, "error")
                return

            if stored_status in ("running", "paused"):
                self._status = "stopped"
                self._processor.set_simulation_status(run_id, "stopped")
            elif stored_status in ("idle", "stopped", "completed", "error"):
                self._status = stored_status
                if stored_status == "error":
                    self._error = (
                        "Предыдущий запуск завершился ошибкой; "
                        "проверьте конфигурацию и попробуйте продолжить"
                    )
            else:
                self._status = "stopped"
                self._processor.set_simulation_status(run_id, "stopped")

    def start(self, speed_multiplier: float = DEFAULT_SPEED_MULTIPLIER) -> dict[str, Any]:
        self._validate_speed(speed_multiplier)
        with self._lock:
            if self._status in ("running", "paused"):
                raise SimulationBusyError("Симуляция уже запущена или приостановлена")

            fingerprint = self._dataset_fingerprint()
            run_id = f"server-sim-{fingerprint}-{uuid4().hex}"
            simulator = self._load_simulator(run_id, 0)
            self._processor.prepare_simulation_run(run_id, speed_multiplier)
            self._run_id = run_id
            self._simulator = simulator
            self._total_readings = len(simulator.readings)
            self._processed_readings = 0
            self._speed_multiplier = speed_multiplier
            self._error = None
            self._set_running_locked()
            return self._status_snapshot_locked()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._status != "running" or self._simulator is None:
                raise SimulationStateError("Приостановить можно только запущенную симуляцию")
            self._simulator.pause()
            self._status = "paused"
            assert self._run_id is not None
            self._processor.set_simulation_status(self._run_id, self._status)
            self._processed_readings = self._processor.simulation_progress(
                self._run_id
            )
            if self._processed_readings >= self._total_readings:
                self._status = "completed"
                self._processor.set_simulation_status(
                    self._run_id,
                    self._status,
                )
            return self._status_snapshot_locked()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._status not in ("paused", "stopped", "error"):
                raise SimulationStateError("Нет приостановленной симуляции для продолжения")
            if self._run_id is None:
                raise SimulationStateError("Нет сохранённой симуляции для продолжения")

            active_thread = (
                self._simulator is not None
                and self._thread is not None
                and self._thread.is_alive()
            )
            if active_thread:
                self._processed_readings = self._processor.simulation_progress(
                    self._run_id
                )
                if self._processed_readings >= self._total_readings:
                    self._status = "completed"
                    self._processor.set_simulation_status(
                        self._run_id,
                        self._status,
                    )
                    return self._status_snapshot_locked()
                assert self._simulator is not None
                self._simulator.resume()
                self._status = "running"
                self._processor.set_simulation_status(self._run_id, self._status)
                return self._status_snapshot_locked()

            self._processed_readings = self._processor.simulation_progress(self._run_id)
            self._simulator = self._load_simulator(
                self._run_id,
                self._processed_readings,
            )
            self._total_readings = len(self._simulator.readings)
            if self._processed_readings >= self._total_readings:
                self._status = "completed"
                self._processor.set_simulation_status(
                    self._run_id,
                    self._status,
                )
                return self._status_snapshot_locked()
            self._set_running_locked()
            return self._status_snapshot_locked()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._status not in ("running", "paused"):
                raise SimulationStateError("Остановить можно только активную симуляцию")
            if self._simulator is not None:
                self._simulator.stop()
            self._status = "stopped"
            assert self._run_id is not None
            self._processed_readings = self._processor.simulation_progress(self._run_id)
            if self._processed_readings >= self._total_readings:
                self._status = "completed"
            self._processor.set_simulation_status(self._run_id, self._status)
            return self._status_snapshot_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            run_id = self._run_id
        if run_id is not None:
            processed = self._processor.simulation_progress(run_id)
            with self._lock:
                if self._run_id == run_id:
                    self._processed_readings = processed
                    if (
                        self._status == "running"
                        and self._thread is not None
                        and not self._thread.is_alive()
                    ):
                        self._finish_from_progress_locked()
                return self._status_snapshot_locked()
        with self._lock:
            return self._status_snapshot_locked()

    def shutdown(self) -> None:
        with self._lock:
            simulator = self._simulator
            thread = self._thread
            run_id = self._run_id
            active = self._status in ("running", "paused")
            if active and simulator is not None:
                simulator.stop()
                self._status = "stopped"
        if thread is not None and thread.is_alive():
            thread.join()
        if active and run_id is not None:
            self._processed_readings = self._processor.simulation_progress(run_id)
            with self._lock:
                self._status = (
                    "completed"
                    if self._processed_readings >= self._total_readings
                    else "stopped"
                )
                final_status = self._status
            self._processor.set_simulation_status(run_id, final_status)

    def _load_simulator(self, run_id: str, start_index: int) -> _PipelineSimulator:
        try:
            fingerprint = self._dataset_fingerprint()
            if not run_id.startswith(f"server-sim-{fingerprint}-"):
                raise SimulationConfigurationError(
                    "CSV или часовой пояс отличаются от использованных при запуске; "
                    "безопасное продолжение невозможно"
                )
            return _PipelineSimulator(
                self._processor,
                self.csv_path,
                run_id=run_id,
                timezone_name=self.timezone_name,
                start_index=start_index,
            )
        except SimulationConfigurationError:
            raise
        except (OSError, ValueError) as exc:
            raise SimulationConfigurationError(str(exc)) from exc

    def _dataset_fingerprint(self) -> str:
        if KRISTINA_DATA_DIR not in self.csv_path.parents:
            raise SimulationConfigurationError(
                "CSV симулятора должен находиться в data/kristina/"
            )
        if not self.timezone_name:
            raise SimulationConfigurationError(
                "Задайте часовой пояс исходного CSV через CGM_SIMULATOR_TIMEZONE"
            )
        try:
            digest = sha256(self.csv_path.read_bytes())
        except OSError as exc:
            raise SimulationConfigurationError(
                f"Не удалось прочитать CSV симулятора: {exc}"
            ) from exc
        digest.update(b"\0")
        digest.update(self.timezone_name.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _validate_speed(speed_multiplier: float) -> None:
        if (
            isinstance(speed_multiplier, bool)
            or not isinstance(speed_multiplier, (int, float))
            or not 0 < speed_multiplier <= MAX_SPEED_MULTIPLIER
        ):
            raise SimulationConfigurationError(
                f"Скорость должна быть > 0 и не превышать {MAX_SPEED_MULTIPLIER:g}"
            )

    def _set_running_locked(self) -> None:
        assert self._run_id is not None
        assert self._simulator is not None
        self._status = "running"
        self._processor.set_simulation_status(
            self._run_id,
            "running",
            self._speed_multiplier,
        )
        self._thread = Thread(
            target=self._run,
            args=(self._simulator, self._speed_multiplier),
            name=f"cgm-simulation-{self._run_id}",
            daemon=False,
        )
        self._thread.start()

    def _run(self, simulator: _PipelineSimulator, speed_multiplier: float) -> None:
        try:
            simulator.run_simulation(
                speed_multiplier=speed_multiplier,
                log_every=max(len(simulator.readings), 1),
            )
        except Exception as exc:
            with self._lock:
                self._status = "error"
                self._error = str(exc)
                if self._run_id is not None:
                    self._processed_readings = self._processor.simulation_progress(
                        self._run_id
                    )
                    self._processor.set_simulation_status(self._run_id, "error")
            return

        with self._lock:
            if self._run_id is not None:
                self._processed_readings = self._processor.simulation_progress(
                    self._run_id
                )
                if self._status not in ("stopped", "error"):
                    self._finish_from_progress_locked()
                self._processor.set_simulation_status(self._run_id, self._status)

    def _finish_from_progress_locked(self) -> None:
        self._status = (
            "completed"
            if self._processed_readings >= self._total_readings
            else "stopped"
        )

    def _status_snapshot_locked(self) -> dict[str, Any]:
        return {
            "run_id": self._run_id,
            "status": self._status,
            "total_readings": self._total_readings,
            "processed_readings": self._processed_readings,
            "current_source_index": self._processed_readings,
            "speed_multiplier": self._speed_multiplier,
            "error": self._error,
        }
