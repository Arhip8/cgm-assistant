import type { Reading } from "../types/api";

interface GlucoseCardProps {
  reading: Reading | null;
}

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Время неизвестно"
    : dateTimeFormatter.format(date);
}

export function GlucoseCard({ reading }: GlucoseCardProps) {
  return (
    <section className="panel glucose-card" aria-labelledby="latest-reading-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">ПОСЛЕДНЕЕ ИЗМЕРЕНИЕ</p>
          <h2 id="latest-reading-title">Уровень глюкозы</h2>
        </div>
        <span className="metric-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none">
            <path
              d="M12 3.5s-6.5 7-6.5 11.2a6.5 6.5 0 0 0 13 0C18.5 10.5 12 3.5 12 3.5Z"
              stroke="currentColor"
              strokeWidth="1.7"
            />
            <path
              d="M9 15.5c.3 1.4 1.2 2.2 2.5 2.5"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            />
          </svg>
        </span>
      </div>

      {reading ? (
        <>
          <div className="glucose-value">
            <span>{reading.glucose_mmol_l.toFixed(1)}</span>
            <span className="glucose-unit">ммоль/л</span>
          </div>
          <div className="reading-meta">
            <span className="meta-dot" />
            <time dateTime={reading.measured_at}>
              {formatDateTime(reading.measured_at)}
            </time>
            <span className="meta-divider">·</span>
            <span>измерение #{reading.source_index}</span>
          </div>
        </>
      ) : (
        <div className="empty-reading">
          <span className="empty-reading-value">—</span>
          <span>Пока нет измерений для этого сеанса</span>
        </div>
      )}
    </section>
  );
}
