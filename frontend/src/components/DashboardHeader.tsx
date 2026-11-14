import { useEffect, useRef, useState } from "react";

import { getSimulationRuns } from "../api/client";
import type { SimulationRunSummary } from "../types/api";
import { formatDateTime } from "./GlucoseCard";

interface DashboardHeaderProps {
  runId: string;
  onRunIdChange: (runId: string) => void;
  activeRunId: string | null;
  connectionStatus: "online" | "offline" | "error" | "checking";
  connectionError: string | null;
}

export function DashboardHeader({
  runId,
  onRunIdChange,
  activeRunId,
  connectionStatus,
  connectionError,
}: DashboardHeaderProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<SimulationRunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const copyTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!historyOpen) return;
    const controller = new AbortController();
    setHistoryLoading(true);
    void getSimulationRuns(controller.signal)
      .then((runs) => {
        setHistory(runs);
        setHistoryError(null);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setHistoryError(
          error instanceof Error ? error.message : "Не удалось загрузить сеансы.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [activeRunId, historyOpen]);

  useEffect(
    () => () => {
      if (copyTimer.current !== undefined) window.clearTimeout(copyTimer.current);
    },
    [],
  );

  const copyRunId = async () => {
    try {
      await navigator.clipboard.writeText(runId);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }

    if (copyTimer.current !== undefined) window.clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopyState("idle"), 1800);
  };

  const connectionLabel = {
    online: "API",
    offline: "Нет связи",
    error: "Ошибка API",
    checking: "Подключение",
  }[connectionStatus];
  const connectionClass = {
    online: "connection-online",
    offline: "connection-offline",
    error: "connection-api-error",
    checking: "connection-pending",
  }[connectionStatus];

  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="CGM Assistant — главная">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" fill="none">
            <path
              d="M16 5.8a5 5 0 0 0-5 5v3.3c0 1.8-.7 3.5-2 4.7h14c-1.3-1.2-2-2.9-2-4.7v-3.3a5 5 0 0 0-5-5Z"
              fill="#FFFFFF"
            />
            <path
              d="M14.8 19.2h2.4v.9a1.2 1.2 0 0 1-2.4 0v-.9Z"
              fill="#FFFFFF"
            />
            <path
              d="M22.8 4.3c-.9 1.4-2.4 3.1-2.4 4.4a2.4 2.4 0 0 0 4.8 0c0-1.3-1.5-3-2.4-4.4Z"
              fill="#F87171"
              stroke="#2563EB"
              strokeWidth="1.2"
            />
          </svg>
        </span>
        <span className="brand-name">
          <strong>cgm</strong>
          <span>assistant</span>
        </span>
      </a>

      <div className="header-controls">
        <span
          className={`connection-indicator ${connectionClass}`}
          title={connectionError ?? connectionLabel}
          aria-label={connectionError ?? connectionLabel}
        >
          <span aria-hidden="true" />
          {connectionLabel}
        </span>
        <label className="run-picker">
          <span>Сеанс</span>
          <input
            aria-label="Идентификатор сеанса"
            value={runId}
            onChange={(event) => onRunIdChange(event.target.value)}
            placeholder="Введите run_id"
            title={runId || "Идентификатор текущего сеанса"}
            autoComplete="off"
            spellCheck={false}
          />
          <button
            className="copy-run-id"
            type="button"
            disabled={!runId}
            onClick={() => void copyRunId()}
            aria-label="Скопировать run_id"
            title={
              copyState === "copied"
                ? "Скопировано"
                : copyState === "error"
                  ? "Не удалось скопировать"
                  : "Скопировать run_id"
            }
          >
            {copyState === "copied" ? (
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path d="m4 10 4 4 8-8" />
              </svg>
            ) : copyState === "error" ? (
              <span aria-hidden="true">!</span>
            ) : (
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <rect x="7" y="6" width="9" height="11" rx="1.5" />
                <path d="M12 3H5.5A1.5 1.5 0 0 0 4 4.5v9A1.5 1.5 0 0 0 5.5 15H6" />
              </svg>
            )}
          </button>
        </label>
        <details
          className="session-history"
          onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
        >
          <summary>История</summary>
          <div className="session-history-menu">
            {activeRunId && activeRunId !== runId && (
              <button
                type="button"
                className="session-history-return"
                onClick={() => onRunIdChange(activeRunId)}
              >
                К текущему сеансу
              </button>
            )}
            {historyError && <p role="alert">{historyError}</p>}
            {historyLoading && <p>Загрузка сеансов…</p>}
            {!historyLoading && !historyError && history.length === 0 && (
              <p>Сеансов пока нет</p>
            )}
            {!historyLoading && history.map((item) => (
              <button
                className="session-history-item"
                type="button"
                key={item.run_id}
                title={item.run_id}
                aria-current={item.run_id === runId ? "true" : undefined}
                onClick={() => {
                  onRunIdChange(item.run_id);
                  setHistoryOpen(false);
                  const details = document.querySelector(".session-history");
                  if (details instanceof HTMLDetailsElement) details.open = false;
                }}
              >
                <strong>{item.run_id.slice(-8)}</strong>
                <span>{formatDateTime(item.started_at)}</span>
              </button>
            ))}
          </div>
        </details>
        <span className="prototype-chip">
          Демо
        </span>
      </div>
    </header>
  );
}
