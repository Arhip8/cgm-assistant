import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Reading } from "../types/api";

interface GlucoseChartProps {
  readings: Reading[];
}

interface ChartReading extends Reading {
  timestamp: number;
}

const timeFormatter = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
});

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

const readingCountLabels: Record<Intl.LDMLPluralRule, string> = {
  zero: "измерений",
  one: "измерение",
  two: "измерения",
  few: "измерения",
  many: "измерений",
  other: "измерения",
};

const readingCountRules = new Intl.PluralRules("ru-RU");

function formatTick(timestamp: number): string {
  return timeFormatter.format(timestamp);
}

function formatReadingCount(count: number): string {
  return `${count} ${readingCountLabels[readingCountRules.select(count)]}`;
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartReading }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;

  return (
    <div className="chart-tooltip">
      <strong>{point.glucose_mmol_l.toFixed(1)} ммоль/л</strong>
      <span>{dateTimeFormatter.format(point.timestamp)}</span>
    </div>
  );
}

export function GlucoseChart({ readings }: GlucoseChartProps) {
  const data = readings
    .map((reading) => ({
      ...reading,
      timestamp: new Date(reading.measured_at).getTime(),
    }))
    .filter((reading) => Number.isFinite(reading.timestamp))
    .sort((left, right) => left.timestamp - right.timestamp);

  return (
    <section className="panel chart-panel" aria-labelledby="chart-title">
      <div className="panel-heading chart-heading">
        <div>
          <p className="eyebrow">ДИНАМИКА</p>
          <h2 id="chart-title">История глюкозы</h2>
        </div>
        <span className="chart-legend">
          <span className="legend-line" />
          ммоль/л
        </span>
      </div>

      {data.length === 0 ? (
        <div className="chart-empty">
          <span className="chart-empty-icon" aria-hidden="true">
            <svg viewBox="0 0 48 48" fill="none">
              <path
                d="M5 25h10l5-12 8 24 6-14h9"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <strong>График появится после первых данных</strong>
          <span>Ожидаем измерения от выбранного сеанса</span>
        </div>
      ) : (
        <div
          className="chart-container"
          role="img"
          aria-label={`График глюкозы: ${data.length} измерений`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={data}
              margin={{ top: 16, right: 12, bottom: 4, left: -12 }}
            >
              <CartesianGrid
                stroke="#e9eeeb"
                strokeDasharray="3 5"
                vertical={false}
              />
              <XAxis
                dataKey="timestamp"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={formatTick}
                tick={{ fill: "#87938e", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                minTickGap={28}
              />
              <YAxis
                dataKey="glucose_mmol_l"
                type="number"
                domain={["auto", "auto"]}
                tick={{ fill: "#87938e", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickCount={5}
                width={42}
              />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ stroke: "#d8e2dc", strokeDasharray: "4 4" }}
              />
              <Line
                type="monotone"
                dataKey="glucose_mmol_l"
                stroke="#188a69"
                strokeWidth={2.5}
                dot={{ r: 3, fill: "#188a69", strokeWidth: 0 }}
                activeDot={{ r: 5, fill: "#188a69", stroke: "#fff", strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="chart-footnote">
        <span>{formatReadingCount(data.length)}</span>
        <span>Обновление каждые 2 секунды</span>
      </div>
    </section>
  );
}
