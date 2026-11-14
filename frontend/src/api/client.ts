import type {
  AnalysisEvent,
  InformationNotification,
  Reading,
  SimulationAction,
  SimulationStatus,
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

async function getApiError(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body
    ) {
      const { detail } = body;
      const prefix = `Ошибка API ${response.status}: `;
      if (typeof detail === "string") return `${prefix}${detail}`;
      if (Array.isArray(detail)) {
        const message = detail
          .map((item) => {
            if (typeof item !== "object" || item === null) return String(item);
            const location = Array.isArray(item.loc)
              ? item.loc.join(".")
              : "";
            const message = typeof item.msg === "string" ? item.msg : "Некорректное значение";
            return location ? `${location}: ${message}` : message;
          })
          .join("; ");
        return `${prefix}${message}`;
      }
    }
  } catch {
    // The response may not contain JSON; use its HTTP status below.
  }
  return `Ошибка API: ${response.status} ${response.statusText}`;
}

async function simulationRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new Error(
      error instanceof Error
        ? `Сетевая ошибка: ${error.message}`
        : "Сетевая ошибка: не удалось связаться с API.",
    );
  }

  if (!response.ok) {
    throw new Error(await getApiError(response));
  }
  return (await response.json()) as T;
}

export function getSimulationStatus(
  signal?: AbortSignal,
): Promise<SimulationStatus> {
  return simulationRequest<SimulationStatus>("/api/v1/simulation/status", {
    signal,
  });
}

export function controlSimulation(
  action: SimulationAction,
  speedMultiplier?: number,
  signal?: AbortSignal,
): Promise<SimulationStatus> {
  return simulationRequest<SimulationStatus>(
    `/api/v1/simulation/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body:
        action === "start"
          ? JSON.stringify({ speed_multiplier: speedMultiplier })
          : undefined,
      signal,
    },
  );
}
