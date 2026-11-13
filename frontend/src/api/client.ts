import type {
  AnalysisEvent,
  InformationNotification,
  Reading,
} from "../types/api";

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    throw new Error(`Ошибка API: ${response.status} ${response.statusText}`);
  }

  return (await response.json()) as T;
}

export function getReadings(
  runId: string,
  signal: AbortSignal,
): Promise<Reading[]> {
  const query = new URLSearchParams({ run_id: runId });
  return getJson<Reading[]>(`/api/v1/readings?${query}`, signal);
}

export function getEvents(
  runId: string,
  signal: AbortSignal,
): Promise<AnalysisEvent[]> {
  const query = new URLSearchParams({ run_id: runId });
  return getJson<AnalysisEvent[]>(`/api/v1/events?${query}`, signal);
}

export function getNotifications(
  runId: string,
  signal: AbortSignal,
): Promise<InformationNotification[]> {
  const query = new URLSearchParams({ run_id: runId });
  return getJson<InformationNotification[]>(
    `/api/v1/notifications?${query}`,
    signal,
  );
}

export async function acknowledgeNotification(
  notificationId: string,
  signal?: AbortSignal,
): Promise<InformationNotification> {
  const response = await fetch(
    `/api/v1/notifications/${encodeURIComponent(notificationId)}/acknowledge`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    },
  );

  if (!response.ok) {
    throw new Error(`Не удалось подтвердить уведомление: ${response.status}`);
  }

  return (await response.json()) as InformationNotification;
}
