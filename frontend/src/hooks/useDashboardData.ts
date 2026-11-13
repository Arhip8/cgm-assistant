import { useCallback, useEffect, useState } from "react";

import {
  acknowledgeNotification as acknowledgeNotificationRequest,
  getEvents,
  getNotifications,
  getReadings,
} from "../api/client";
import type {
  AnalysisEvent,
  InformationNotification,
  Reading,
} from "../types/api";

const POLL_INTERVAL_MS = 2_000;

export interface DashboardData {
  runId: string;
  readings: Reading[];
  events: AnalysisEvent[];
  notifications: InformationNotification[];
  loading: boolean;
  error: string | null;
  lastSuccessfulUpdate: Date | null;
}

function initialData(runId: string): DashboardData {
  return {
    runId,
    readings: [],
    events: [],
    notifications: [],
    loading: true,
    error: null,
    lastSuccessfulUpdate: null,
  };
}

export function useDashboardData(
  runId: string,
): DashboardData & {
  acknowledgeNotification: (notificationId: string) => Promise<void>;
} {
  const [data, setData] = useState<DashboardData>(() => initialData(runId));

  const acknowledge = useCallback(
    async (notificationId: string): Promise<void> => {
      const notification =
        await acknowledgeNotificationRequest(notificationId);
      setData((current) => {
        if (current.runId !== runId) return current;
        return {
          ...current,
          notifications: current.notifications.map((item) =>
            item.id === notification.id ? notification : item,
          ),
        };
      });
    },
    [runId],
  );

  useEffect(() => {
    let active = true;
    let inFlight = false;
    let timeoutId: number | undefined;
    let controller: AbortController | undefined;

    setData(initialData(runId));

    const poll = async (): Promise<void> => {
      if (!active || inFlight) return;

      inFlight = true;
      controller = new AbortController();

      try {
        const [readings, events, notifications] = await Promise.all([
          getReadings(runId, controller.signal),
          getEvents(runId, controller.signal),
          getNotifications(runId, controller.signal),
        ]);

        if (active) {
          setData((current) => {
            const previousNotifications = new Map(
              current.runId === runId
                ? current.notifications.map((item) => [item.id, item])
                : [],
            );
            return {
              runId,
              readings,
              events,
              notifications: notifications.map((notification) => {
                const previous = previousNotifications.get(notification.id);
                return previous?.status === "read" &&
                  notification.status === "unread"
                  ? previous
                  : notification;
              }),
              loading: false,
              error: null,
              lastSuccessfulUpdate: new Date(),
            };
          });
        }
      } catch (error) {
        if (active && !(error instanceof DOMException && error.name === "AbortError")) {
          setData((current) => ({
            ...current,
            runId,
            loading: false,
            error:
              error instanceof Error
                ? error.message
                : "Не удалось получить данные от API.",
          }));
        }
      } finally {
        inFlight = false;
        if (active) {
          timeoutId = window.setTimeout(() => {
            void poll();
          }, POLL_INTERVAL_MS);
        }
      }
    };

    void poll();

    return () => {
      active = false;
      controller?.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [runId]);

  // Do not display the previous run's readings during the render before its
  // effect has reset the polling state.
  return {
    ...(data.runId === runId ? data : initialData(runId)),
    acknowledgeNotification: acknowledge,
  };
}
