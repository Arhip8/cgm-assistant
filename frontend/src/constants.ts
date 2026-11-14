export const DEMO_GLUCOSE_RANGE = {
  lower: 4.0,
  upper: 10.0,
} as const;

export function formatGlucose(value: number): string {
  return value.toLocaleString("ru-RU", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}
