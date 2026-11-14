import type {
  AnalysisEvent,
  InformationNotification,
  Reading,
  SimulationAction,
  SimulationRunSummary,
  SimulationStatus,
} from "../types/api";

export const BACKEND_UNAVAILABLE_MESSAGE =
  "Нет соединения с сервером. Проверьте, запущен ли FastAPI";

async function fetchApi(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new Error(BACKEND_UNAVAILABLE_MESSAGE);
  }
}

async function getApiError(response: Response): Promise<string> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return `Ошибка API: ${response.status} ${response.statusText}`;
  }

  if (typeof body !== "object" || body === null) {
    return `Ошибка API: ${response.status} ${response.statusText}`;
  }

  if ("code" in body && body.code === "BACKEND_UNAVAILABLE") {
    return BACKEND_UNAVAILABLE_MESSAGE;
  }

  if ("detail" in body) {
    const prefix = `Ошибка API ${response.status}: `;
    if (typeof body.detail === "string") return `${prefix}${body.detail}`;
    if (Array.isArray(body.detail)) {
      const message = body.detail
        .map((item) => {
          if (typeof item !== "object" || item === null) return String(item);
          const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
          const description =
            typeof item.msg === "string" ? item.msg : "Некорректное значение";
          return location ? `${location}: ${description}` : description;
        })
        .join("; ");
      return `${prefix}${message}`;
    }
  }

  return `Ошибка API: ${response.status} ${response.statusText}`;
}

async function parseApiResponse<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await getApiError(response));
  return (await response.json()) as T;
}

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  return parseApiResponse<T>(await fetchApi(path, { signal }));
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
  const response = await fetchApi(
    `/api/v1/notifications/${encodeURIComponent(notificationId)}/acknowledge`,
    {
      method: "POST",
      signal,
    },
  );

  return parseApiResponse<InformationNotification>(response);
}

export function getSimulationStatus(
  signal?: AbortSignal,
): Promise<SimulationStatus> {
  return fetchApi("/api/v1/simulation/status", { signal }).then(
    parseApiResponse<SimulationStatus>,
  );
}

export function getSimulationRuns(
  signal?: AbortSignal,
): Promise<SimulationRunSummary[]> {
  return fetchApi("/api/v1/simulation/runs", { signal }).then(
    parseApiResponse<SimulationRunSummary[]>,
  );
}

export function controlSimulation(
  action: SimulationAction,
  speedMultiplier?: number,
  signal?: AbortSignal,
): Promise<SimulationStatus> {
  return fetchApi(
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
  ).then(parseApiResponse<SimulationStatus>);
}
