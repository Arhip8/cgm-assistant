import type { DashboardData } from "../hooks/useDashboardData";

interface StatusPanelProps {
  data: DashboardData;
  runId: string;
}

const updateFormatter = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export function StatusPanel({ data, runId }: StatusPanelProps) {
  const apiStatus = data.error
    ? "error"
    : data.lastSuccessfulUpdate
      ? "online"
      : "checking";

  return (
    <section className="status-panel" aria-label="Статус данных">
      <div className="status-main">
        <span className={`status-indicator status-${apiStatus}`} />
        <div>
          <strong>
            {apiStatus === "online"
              ? "API доступен"
              : apiStatus === "error"
                ? "Нет соединения с API"
                : "Подключаемся к API"}
          </strong>
          <span>
            {data.error
              ? data.error
              : data.lastSuccessfulUpdate
                ? `Сеанс ${runId || "не указан"}`
                : "Ожидаем первый ответ"}
          </span>
        </div>
      </div>
      <div className="status-update">
        <span>Обновлено</span>
        <strong>
          {data.lastSuccessfulUpdate
            ? updateFormatter.format(data.lastSuccessfulUpdate)
            : "—"}
        </strong>
      </div>
    </section>
  );
}
