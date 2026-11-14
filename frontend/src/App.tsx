import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { ActivityPanel } from "./components/ActivityPanel";
import { DashboardHeader } from "./components/DashboardHeader";
import { GlucoseCard } from "./components/GlucoseCard";
import { SimulationControlPanel } from "./components/SimulationControlPanel";
import { useDashboardData } from "./hooks/useDashboardData";
import { publicDemo, BACKEND_UNAVAILABLE_MESSAGE } from "./api/client";

const GlucoseChart = lazy(() =>
  import("./components/GlucoseChart").then((module) => ({
    default: module.GlucoseChart,
  })),
);

function App() {
  const [runId, setRunId] = useState(publicDemo ? "" : "demo-001");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const data = useDashboardData(runId);
  useEffect(() => {
    const reset = () => { setRunId(""); setActiveRunId(null); };
    window.addEventListener("cgm-session-reset", reset);
    return () => window.removeEventListener("cgm-session-reset", reset);
  }, []);
  const syncActiveRunId = useCallback((nextRunId: string) => {
    setActiveRunId(nextRunId);
    setRunId(nextRunId);
  }, []);

  const recentReadings = useMemo(
    () =>
      [...data.readings]
        .filter((reading) =>
          Number.isFinite(new Date(reading.measured_at).getTime()),
        )
        .sort(
          (left, right) =>
            new Date(left.measured_at).getTime() -
            new Date(right.measured_at).getTime(),
        ),
    [data.readings],
  );
  const latestReading = recentReadings.at(-1) ?? null;
  const previousReading = recentReadings.at(-2) ?? null;
  const connectionStatus = data.error
    ? data.error === BACKEND_UNAVAILABLE_MESSAGE
      ? "offline"
      : "error"
    : data.lastSuccessfulUpdate
      ? "online"
      : "checking";

  return (
    <div className="app-shell">
      <DashboardHeader
        runId={runId}
        onRunIdChange={setRunId}
        activeRunId={activeRunId}
        connectionStatus={connectionStatus}
        connectionError={data.error}
      />

      <main className="dashboard">
        {activeRunId && activeRunId !== runId && (
          <p className="history-view-notice" role="status">
            Просмотр истории. Активная симуляция не изменена.
            <button type="button" onClick={() => setRunId(activeRunId)}>
              К текущему сеансу
            </button>
          </p>
        )}
        <div className="overview-grid">
          <GlucoseCard reading={latestReading} previousReading={previousReading} />
          <SimulationControlPanel
            onRunIdChange={syncActiveRunId}
            sharedError={data.error}
          />
          <section className="summary-panel" aria-label="Сводка">
            <h2>Сводка</h2>
            <div className="summary-stat">
              <span>События</span>
              <strong>{data.events.length.toLocaleString("ru-RU")}</strong>
            </div>
            <div className="summary-stat">
              <span>Уведомления</span>
              <strong>{data.notifications.length.toLocaleString("ru-RU")}</strong>
            </div>
            <div className="summary-stat summary-stat-new">
              <span>Новые</span>
              <strong>
                {data.notifications
                  .filter((notification) => notification.status === "unread")
                  .length.toLocaleString("ru-RU")}
              </strong>
            </div>
          </section>
        </div>

        <p className="prototype-warning">
          {publicDemo ? "Синтетические данные. Демонстрационный алгоритм. Данные могут исчезнуть после перезапуска сервера. На ×60 сценарий занимает 2–3 минуты: нажмите «Я увидел» после первого уведомления." : "Демонстрационный прототип."} Не для медицинских решений
        </p>
        {data.error && (
          <p className="connection-message" role="alert">
            {data.error}
          </p>
        )}

        <div className="dashboard-grid">
          <aside className="side-column">
            <ActivityPanel
              notifications={data.notifications}
              events={data.events}
              onAcknowledge={data.acknowledgeNotification}
            />
          </aside>
          <div className="main-column">
            <Suspense
              fallback={
                <section className="panel chart-loading" aria-live="polite">
                  Загружаем график…
                </section>
              }
            >
              <GlucoseChart readings={data.readings} />
            </Suspense>
          </div>
        </div>

      </main>
    </div>
  );
}

export default App;
