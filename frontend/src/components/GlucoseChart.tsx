import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Reading } from "../types/api";
import { DEMO_GLUCOSE_RANGE, formatGlucose } from "../constants";

interface GlucoseChartProps {
  readings: Reading[];
}

interface ChartReading extends Reading {
  timestamp: number;
}

const timeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const MAX_CONTINUOUS_GAP_MS = 15 * 60 * 1000;

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
      <strong>{formatGlucose(point.glucose_mmol_l)} ммоль/л</strong>
      <span>{dateTimeFormatter.format(point.timestamp)}</span>
    </div>
  );
}

export function GlucoseChart({ readings }: GlucoseChartProps) {
  const sortedReadings = readings
    .map((reading) => ({
      ...reading,
      timestamp: new Date(reading.measured_at).getTime(),
    }))
    .filter((reading) => Number.isFinite(reading.timestamp))
    .sort((left, right) => left.timestamp - right.timestamp);
  const segments: ChartReading[][] = [];
  sortedReadings.forEach((reading, index) => {
    const previous = sortedReadings[index - 1];
    if (
      !previous ||
      reading.timestamp - previous.timestamp > MAX_CONTINUOUS_GAP_MS
    ) {
      segments.push([]);
    }
    segments.at(-1)?.push(reading);
  });
  const values = sortedReadings.map((reading) => reading.glucose_mmol_l);
  const yMin = Math.min(...values, DEMO_GLUCOSE_RANGE.lower);
  const yMax = Math.max(...values, DEMO_GLUCOSE_RANGE.upper);

  return (
    <section className="panel chart-panel" aria-labelledby="chart-title">
      <div className="panel-heading chart-heading">
        <h2 id="chart-title">История глюкозы</h2>
        <span className="chart-legend">
          Демодиапазон {formatGlucose(DEMO_GLUCOSE_RANGE.lower)}–{formatGlucose(DEMO_GLUCOSE_RANGE.upper)} ммоль/л
        </span>
      </div>

      {sortedReadings.length === 0 ? (
        <div className="chart-empty">
          <strong>Нет измерений для отображения</strong>
          <span>Данные появятся после поступления первого измерения</span>
        </div>
      ) : (
        <div
          className="chart-container"
          role="img"
          aria-label={`График глюкозы: ${sortedReadings.length} измерений`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={sortedReadings}
              margin={{ top: 16, right: 12, bottom: 4, left: -12 }}
            >
              <ReferenceArea
                y1={DEMO_GLUCOSE_RANGE.lower}
                y2={DEMO_GLUCOSE_RANGE.upper}
                fill="#0D9488"
                fillOpacity={0.055}
                ifOverflow="extendDomain"
              />
              <CartesianGrid
                stroke="#E2E8F0"
                strokeDasharray="3 5"
                vertical={false}
              />
              <XAxis
                dataKey="timestamp"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={formatTick}
                tick={{ fill: "#64748B", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                minTickGap={28}
              />
              <YAxis
                dataKey="glucose_mmol_l"
                type="number"
                domain={[yMin, yMax]}
                tickFormatter={(value: number) => formatGlucose(value)}
                tick={{ fill: "#64748B", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickCount={5}
                width={48}
                label={{
                  value: "ммоль/л",
                  angle: -90,
                  position: "insideLeft",
                  fill: "#64748B",
                  fontSize: 10,
                }}
              />
              <ReferenceLine
                y={DEMO_GLUCOSE_RANGE.lower}
                stroke="#94A3B8"
                strokeDasharray="4 4"
                ifOverflow="extendDomain"
              />
              <ReferenceLine
                y={DEMO_GLUCOSE_RANGE.upper}
                stroke="#94A3B8"
                strokeDasharray="4 4"
                ifOverflow="extendDomain"
              />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ stroke: "#CBD5E1", strokeDasharray: "4 4" }}
              />
              {segments.map((segment, index) => (
                <Line
                  key={`glucose-segment-${index}`}
                  data={segment}
                  type="linear"
                  dataKey="glucose_mmol_l"
                  stroke="var(--chart)"
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: "var(--chart)", strokeWidth: 0 }}
                  activeDot={{ r: 5, fill: "var(--chart)", stroke: "#fff", strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="chart-footnote">
        <span>{formatReadingCount(sortedReadings.length)}</span>
      </div>
    </section>
  );
}
