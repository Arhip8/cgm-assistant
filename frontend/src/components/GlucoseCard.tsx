import type { Reading } from "../types/api";
import { DEMO_GLUCOSE_RANGE, formatGlucose } from "../constants";

interface GlucoseCardProps {
  reading: Reading | null;
  previousReading: Reading | null;
}

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  day: "numeric",
  month: "short",
});

export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Время неизвестно"
    : dateTimeFormatter.format(date);
}

export function GlucoseCard({ reading, previousReading }: GlucoseCardProps) {
  const rangeState = reading
    ? reading.glucose_mmol_l < DEMO_GLUCOSE_RANGE.lower
      ? "below"
      : reading.glucose_mmol_l > DEMO_GLUCOSE_RANGE.upper
        ? "above"
        : "within"
    : null;
  const change =
    reading && previousReading
      ? reading.glucose_mmol_l - previousReading.glucose_mmol_l
      : null;

  return (
    <section className="panel glucose-card" aria-labelledby="latest-reading-title">
      <h2 id="latest-reading-title">Глюкоза</h2>
      {reading ? (
        <>
          <div className="glucose-value">
            <span>{formatGlucose(reading.glucose_mmol_l)}</span>
            <span className="glucose-unit">ммоль/л</span>
          </div>
          <div className="reading-details">
            <time dateTime={reading.measured_at}>
              {formatDateTime(reading.measured_at)}
            </time>
            <span className={`range-status range-${rangeState}`}>
              {rangeState === "below"
                ? "Ниже демодиапазона"
                : rangeState === "above"
                  ? "Выше демодиапазона"
                  : "В демодиапазоне"}
            </span>
          </div>
          {change !== null && (
            <p className={`reading-change ${change > 0 ? "change-up" : change < 0 ? "change-down" : ""}`}>
              {change > 0 ? "↑" : change < 0 ? "↓" : "→"}{" "}
              {change > 0 ? "+" : ""}
              {formatGlucose(change)} к предыдущему измерению
            </p>
          )}
        </>
      ) : (
        <div className="empty-reading">
          <span>— ммоль/л</span>
          <span>Ожидание измерений</span>
        </div>
      )}
      <p className="demo-range-caption">
        Демограницы: {formatGlucose(DEMO_GLUCOSE_RANGE.lower)}–{formatGlucose(DEMO_GLUCOSE_RANGE.upper)} ммоль/л
      </p>
    </section>
  );
}
