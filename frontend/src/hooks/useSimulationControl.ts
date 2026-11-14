import { useCallback, useEffect, useRef, useState } from "react";

import {
  controlSimulation,
  getSimulationStatus,
} from "../api/client";
import type {
  SimulationAction,
  SimulationStatus,
} from "../types/api";

const POLL_INTERVAL_MS = 2_000;

export interface SimulationControl {
  status: SimulationStatus | null;
  loading: boolean;
  pendingAction: SimulationAction | null;
  error: string | null;
  runAction: (action: SimulationAction, speedMultiplier?: number) => Promise<void>;
}

export function useSimulationControl(
  onRunIdChange: (runId: string) => void,
): SimulationControl {
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingAction, setPendingAction] = useState<SimulationAction | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const runIdChangeRef = useRef(onRunIdChange);
  const restoredRef = useRef(false);
  const pausePollingRef = useRef<() => void>(() => {});
  const resumePollingRef = useRef<() => void>(() => {});

  useEffect(() => {
    runIdChangeRef.current = onRunIdChange;
  }, [onRunIdChange]);

  useEffect(() => {
    const reset = () => { restoredRef.current = false; };
    window.addEventListener("cgm-session-reset", reset);
    let active = true;
    let requestInFlight = false;
    let timeoutId: number | undefined;
    let controller: AbortController | undefined;
    let revision = 0;
    let pausedForAction = false;

    let poll: () => Promise<void>;
    const schedulePoll = (delay = POLL_INTERVAL_MS): void => {
      if (!active || pausedForAction) return;
      timeoutId = window.setTimeout(() => {
        void poll();
      }, delay);
    };

    poll = async (): Promise<void> => {
      if (!active || pausedForAction || requestInFlight) return;
      requestInFlight = true;
      const requestRevision = revision;
      controller = new AbortController();
      try {
        const nextStatus = await getSimulationStatus(controller.signal);
        if (active && requestRevision === revision) {
          setStatus(nextStatus);
          setError(null);
          setLoading(false);
          if (!restoredRef.current) {
            restoredRef.current = true;
            runIdChangeRef.current(nextStatus.run_id ?? "");
          }
        }
      } catch (pollError) {
        if (
          active &&
          requestRevision === revision &&
          !(pollError instanceof DOMException && pollError.name === "AbortError")
        ) {
          setError(
            pollError instanceof Error
              ? pollError.message
              : "Не удалось получить состояние симуляции.",
          );
          setLoading(false);
        }
      } finally {
        if (requestRevision === revision) {
          requestInFlight = false;
          schedulePoll();
        }
      }
    };

    pausePollingRef.current = () => {
      pausedForAction = true;
      revision += 1;
      controller?.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      requestInFlight = false;
    };
    resumePollingRef.current = () => {
      pausedForAction = false;
      void poll();
    };

    void poll();

    return () => {
      window.removeEventListener("cgm-session-reset", reset);
      active = false;
      controller?.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      pausePollingRef.current = () => {};
      resumePollingRef.current = () => {};
    };
  }, []);

  const runAction = useCallback(
    async (
      action: SimulationAction,
      speedMultiplier?: number,
    ): Promise<void> => {
      pausePollingRef.current();
      setPendingAction(action);
      setError(null);
      try {
        const nextStatus = await controlSimulation(action, speedMultiplier);
        setStatus(nextStatus);
        if (nextStatus.run_id) runIdChangeRef.current(nextStatus.run_id);
      } catch (actionError) {
        setError(
          actionError instanceof Error
            ? actionError.message
            : "Не удалось выполнить команду симуляции.",
        );
      } finally {
        setPendingAction(null);
        resumePollingRef.current();
      }
    },
    [],
  );

  return { status, loading, pendingAction, error, runAction };
}
