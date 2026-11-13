import { lazy, Suspense, useMemo, useState } from "react";

import { DashboardHeader } from "./components/DashboardHeader";
import { EventsPanel } from "./components/EventsPanel";
import { GlucoseCard } from "./components/GlucoseCard";
import { NotificationsPanel } from "./components/NotificationsPanel";
import { StatusPanel } from "./components/StatusPanel";
import { useDashboardData } from "./hooks/useDashboardData";

const GlucoseChart = lazy(() =>
  import("./components/GlucoseChart").then((module) => ({
    default: module.GlucoseChart,
  })),
);

function App() {
  const [runId, setRunId] = useState("demo-001");
  const data = useDashboardData(runId);

  const latestReading = useMemo(
    () =>
      [...data.readings]
        .filter((reading) => Number.isFinite(new Date(reading.measured_at).getTime()))
        .sort(
          (left, right) =>
            new Date(right.measured_at).getTime() -
            new Date(left.measured_at).getTime(),
        )[0] ?? null,
    [data.readings],
  );

  return (
    <div className="app-shell">
      <DashboardHeader runId={runId} onRunIdChange={setRunId} />

      <main className="dashboard">
        <div className="page-intro">
          <div>
            <p className="eyebrow page-eyebrow">CGM ASSISTANT · МОНИТОРИНГ</p>
            <h1>Панель наблюдения</h1>
            <p className="page-description">
              История измерений и наблюдения демонстрационного алгоритма
            </p>
          </div>
          <div className="prototype-notice">
            <span className="notice-icon" aria-hidden="true">i</span>
            Демонстрационный прототип
            <span className="notice-separator">·</span>
            не для медицинских решений
          </div>
        </div>

        <StatusPanel data={data} runId={runId} />

        {data.error && data.readings.length === 0 && !data.lastSuccessfulUpdate && (
          <div className="connection-error" role="alert">
            <span className="error-icon" aria-hidden="true">!</span>
            <div>
              <strong>Не удалось загрузить данные</strong>
              <span>
                Проверьте, что backend запущен, и попробуйте снова через пару
                секунд.
              </span>
            </div>
          </div>
        )}

        <div className="dashboard-grid">
          <div className="main-column">
            <GlucoseCard reading={latestReading} />
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
          <aside className="side-column">
            <NotificationsPanel
              notifications={data.notifications}
              onAcknowledge={data.acknowledgeNotification}
            />
            <EventsPanel events={data.events} />
            <div className="data-note">
              <span className="data-note-icon" aria-hidden="true">↗</span>
              <p>
                Данные поступают из выбранного сеанса. История и события
                обновляются автоматически.
              </p>
            </div>
          </aside>
        </div>

        <footer className="dashboard-footer">
          <span>CGM Assistant</span>
          <span>Демонстрационный прототип · данные хранятся временно</span>
        </footer>
      </main>
    </div>
  );
}

export default App;
