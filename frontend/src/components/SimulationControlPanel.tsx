import { useEffect, useRef, useState } from "react";

import { useSimulationControl } from "../hooks/useSimulationControl";
import type { SimulationAction, SimulationState } from "../types/api";

interface SimulationControlPanelProps {
  onRunIdChange: (runId: string) => void;
}

const SPEED_OPTIONS = [1, 10, 60, 300, 3600] as const;
const STATE_LABELS: Record<SimulationState, string> = {
  idle: "Ожидает запуска",
  running: "Воспроизводится",
  paused: "На паузе",
  stopped: "Остановлена",
  completed: "Завершена",
  error: "Ошибка",
};

const ACTION_LABELS: Record<SimulationAction, string> = {
  start: "Запустить",
  pause: "Пауза",
  resume: "Продолжить",
  stop: "Остановить",
};

export function SimulationControlPanel({
  onRunIdChange,
}: SimulationControlPanelProps) {
  const [speedMultiplier, setSpeedMultiplier] = useState<number>(60);
  const simulation = useSimulationControl(onRunIdChange);
  const speedRestored = useRef(false);
  const status = simulation.status;
  const state = status?.status ?? "idle";
  const isActive = state === "running" || state === "paused";
  const percent =
    status && status.total_readings > 0
      ? Math.min(
          100,
          Math.round((status.processed_readings / status.total_readings) * 100),
        )
      : 0;
  const disabled = simulation.loading || simulation.pendingAction !== null;

  useEffect(() => {
    if (speedRestored.current || !status) return;
    speedRestored.current = true;
    if (SPEED_OPTIONS.some((speed) => speed === status.speed_multiplier)) {
      setSpeedMultiplier(status.speed_multiplier);
    }
  }, [status]);

  const run = (action: SimulationAction) => {
    void simulation.runAction(
      action,
      action === "start" ? speedMultiplier : undefined,
    );
  };

  return (
    <section className="panel simulation-panel" aria-labelledby="simulation-title">
      <div className="simulation-heading">
        <div>
          <p className="eyebrow">ИСТОРИЧЕСКИЕ ДАННЫЕ</p>
          <h2 id="simulation-title">Управление симуляцией</h2>
        </div>
        <span className={`simulation-state simulation-state-${state}`}>
          <span className="simulation-state-dot" aria-hidden="true" />
          {simulation.loading ? "Загрузка состояния…" : STATE_LABELS[state]}
        </span>
      </div>

      <div className="simulation-progress">
        <div className="simulation-progress-meta">
          <div className="simulation-run-id">
            <span>Запуск</span>
            <strong title={status?.run_id ?? "Не создан"}>
              {status?.run_id ?? "Не создан"}
            </strong>
          </div>
          <strong className="simulation-percent">{percent}%</strong>
        </div>
        <div
          className="simulation-progress-track"
          role="progressbar"
          aria-label="Прогресс симуляции"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <span style={{ width: `${percent}%` }} />
        </div>
        <div className="simulation-counts">
          <span>
            Обработано{" "}
            <strong>{status?.processed_readings.toLocaleString("ru-RU") ?? 0}</strong>
          </span>
          <span>
            Всего{" "}
            <strong>{status?.total_readings.toLocaleString("ru-RU") ?? 0}</strong>
          </span>
        </div>
      </div>

      <div className="simulation-controls">
        <fieldset className="simulation-speed" disabled={disabled || isActive}>
          <legend>Скорость</legend>
          <div className="simulation-speed-options">
            {SPEED_OPTIONS.map((speed) => (
              <button
                key={speed}
                type="button"
                className={
                  speedMultiplier === speed ? "simulation-speed-selected" : ""
                }
                aria-pressed={speedMultiplier === speed}
                onClick={() => setSpeedMultiplier(speed)}
              >
                ×{speed}
              </button>
            ))}
          </div>
        </fieldset>

        <div className="simulation-actions" aria-label="Команды симуляции">
          <button
            type="button"
            className="simulation-action simulation-action-primary"
            disabled={disabled || isActive}
            onClick={() => run("start")}
          >
            {simulation.pendingAction === "start" ? "Запуск…" : ACTION_LABELS.start}
          </button>
          <button
            type="button"
            className="simulation-action"
            disabled={disabled || state !== "running"}
            onClick={() => run("pause")}
          >
            {simulation.pendingAction === "pause" ? "Пауза…" : ACTION_LABELS.pause}
          </button>
          <button
            type="button"
            className="simulation-action"
            disabled={
              disabled ||
              !(state === "paused" || state === "stopped" || state === "error")
            }
            onClick={() => run("resume")}
          >
            {simulation.pendingAction === "resume"
              ? "Продолжение…"
              : ACTION_LABELS.resume}
          </button>
          <button
            type="button"
            className="simulation-action simulation-action-stop"
            disabled={disabled || !(state === "running" || state === "paused")}
            onClick={() => run("stop")}
          >
            {simulation.pendingAction === "stop" ? "Остановка…" : ACTION_LABELS.stop}
          </button>
        </div>
      </div>

      {(simulation.error || status?.error) && (
        <p className="simulation-error" role="alert">
          {simulation.error ?? status?.error}
        </p>
      )}
      {!simulation.error && simulation.pendingAction && (
        <p className="simulation-hint" aria-live="polite">
          Выполняется команда «{ACTION_LABELS[simulation.pendingAction]}»…
        </p>
      )}
    </section>
  );
}
