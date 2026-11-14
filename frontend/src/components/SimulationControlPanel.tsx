import { useEffect, useRef, useState } from "react";

import { useSimulationControl } from "../hooks/useSimulationControl";
import type { SimulationAction, SimulationState } from "../types/api";

interface SimulationControlPanelProps {
  onRunIdChange: (runId: string) => void;
  sharedError?: string | null;
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
  sharedError,
}: SimulationControlPanelProps) {
  const [speedMultiplier, setSpeedMultiplier] = useState<number>(60);
  const [confirmRestart, setConfirmRestart] = useState(false);
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
  const canResume =
    Boolean(status?.run_id) &&
    (status?.processed_readings ?? 0) < (status?.total_readings ?? 0);

  useEffect(() => {
    if (speedRestored.current || !status) return;
    speedRestored.current = true;
    if (SPEED_OPTIONS.some((speed) => speed === status.speed_multiplier)) {
      setSpeedMultiplier(status.speed_multiplier);
    }
  }, [status]);

  const run = async (action: SimulationAction) => {
    await simulation.runAction(
      action,
      action === "start" ? speedMultiplier : undefined,
    );
  };

  const restart = async () => {
    setConfirmRestart(false);
    await run("start");
  };

  return (
    <section className="panel simulation-panel" aria-labelledby="simulation-title">
      <div className="simulation-heading">
        <h2 id="simulation-title">Симуляция</h2>
        <span className={`simulation-state simulation-state-${state}`}>
          {simulation.loading ? "Загрузка…" : STATE_LABELS[state]}
        </span>
      </div>

      <div className="simulation-progress">
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
          <strong>
            {status?.processed_readings.toLocaleString("ru-RU") ?? 0}
          </strong>
          <span>
            из {status?.total_readings.toLocaleString("ru-RU") ?? 0} измерений
          </span>
          <strong className="simulation-percent">{percent}%</strong>
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
          {state === "idle" && (
            <button
              type="button"
              className="simulation-action simulation-action-primary"
              disabled={disabled}
              onClick={() => run("start")}
            >
              {simulation.pendingAction === "start" ? "Запуск…" : ACTION_LABELS.start}
            </button>
          )}
          {state === "running" && (
            <>
              <button
                type="button"
                className="simulation-action simulation-action-primary"
                disabled={disabled}
                onClick={() => run("pause")}
              >
                {simulation.pendingAction === "pause" ? "Пауза…" : ACTION_LABELS.pause}
              </button>
              <button
                type="button"
                className="simulation-action simulation-action-stop"
                disabled={disabled}
                onClick={() => run("stop")}
              >
                {simulation.pendingAction === "stop" ? "Остановка…" : ACTION_LABELS.stop}
              </button>
            </>
          )}
          {(state === "paused" || state === "stopped" || state === "error") &&
            canResume && (
              <>
                <button
                  type="button"
                  className="simulation-action simulation-action-primary"
                  disabled={disabled}
                  onClick={() => run("resume")}
                >
                  {simulation.pendingAction === "resume"
                    ? "Продолжение…"
                    : ACTION_LABELS.resume}
                </button>
                {state === "paused" && (
                  <button
                    type="button"
                    className="simulation-action simulation-action-stop"
                    disabled={disabled}
                    onClick={() => run("stop")}
                  >
                    {simulation.pendingAction === "stop"
                      ? "Остановка…"
                      : ACTION_LABELS.stop}
                  </button>
                )}
              </>
            )}
          {(state === "stopped" || state === "completed" || state === "error") && (
            <button
              type="button"
              className="simulation-action simulation-action-primary"
              disabled={disabled}
              onClick={() => setConfirmRestart(true)}
            >
              Начать заново
            </button>
          )}
        </div>
      </div>

      {confirmRestart && (
        <div
          className="restart-confirmation"
          role="dialog"
          aria-labelledby="restart-confirmation-title"
        >
          <div className="restart-confirmation-content">
            <strong id="restart-confirmation-title">Начать новый сеанс?</strong>
            <p>
              Симуляция начнётся с первого измерения в новом сеансе. Данные
              предыдущего сеанса останутся сохранены.
            </p>
            <div className="restart-confirmation-actions">
              <button
                className="simulation-action"
                type="button"
                disabled={disabled}
                onClick={() => setConfirmRestart(false)}
              >
                Отмена
              </button>
              <button
                className="simulation-action simulation-action-primary"
                type="button"
                disabled={disabled}
                onClick={() => void restart()}
              >
                {simulation.pendingAction === "start" ? "Запуск…" : "Начать"}
              </button>
            </div>
          </div>
        </div>
      )}

      {(simulation.error || status?.error) &&
        (simulation.error ?? status?.error) !== sharedError && (
          <p className="simulation-error" role="alert">
            {simulation.error ?? status?.error}
          </p>
        )}
      {!simulation.error && simulation.pendingAction && (
        <p className="simulation-hint" aria-live="polite">
          Выполняется команда «{ACTION_LABELS[simulation.pendingAction]}»…
        </p>
      )}
      {state === "completed" && (
        <p className="simulation-complete">Все измерения обработаны</p>
      )}
    </section>
  );
}
