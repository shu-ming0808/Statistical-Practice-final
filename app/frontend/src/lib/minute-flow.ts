import type { FlowRow } from "@/lib/api";

export interface MinuteFlow {
  count: number | null;
  intensity: number;
  color: string;
}

const blue = [59, 130, 246];
const red = [239, 68, 68];
const broadWeights = Array.from({ length: 60 }, (_, m) =>
  Math.exp(-0.5 * ((m + 0.5 - 30) / 20) ** 2),
);
const broadTotal = broadWeights.reduce((a, b) => a + b, 0);

export function minuteWeight(minuteInHour: number, peak: boolean): number {
  return peak ? broadWeights[minuteInHour] / broadTotal : 1 / 60;
}

export function densityColor(intensity: number): string {
  const t = Math.max(0, Math.min(1, intensity));
  return `rgb(${blue.map((v, i) => Math.round(v + (red[i] - v) * t)).join(", ")})`;
}

/** Scenario estimate from observed hourly entrances, never measured minute counts. */
export function minuteFlows(rows: FlowRow[], minute: number): Record<string, MinuteFlow> {
  const byStation = new Map<string, FlowRow[]>();
  for (const row of rows) {
    if (!byStation.has(row.station)) byStation.set(row.station, []);
    byStation.get(row.station)!.push(row);
  }
  const peak = new Map<string, number>();
  let maxMinute = 0;
  for (const [station, stationRows] of byStation) {
    const complete = stationRows.filter(r => r.origin_complete && r.origin_count != null);
    const peakRow = complete.reduce<FlowRow | null>((best, r) =>
      !best || r.origin_count! > best.origin_count! ? r : best, null);
    if (peakRow) peak.set(station, Number(peakRow.hour_start.slice(11, 13)));
    for (const row of complete) {
      const hour = Number(row.hour_start.slice(11, 13));
      maxMinute = Math.max(maxMinute, row.origin_count! * minuteWeight(29, hour === peak.get(station)));
    }
  }
  const currentHour = 18 + Math.floor(minute / 60);
  const minuteInHour = Math.floor(minute % 60);
  const result: Record<string, MinuteFlow> = {};
  for (const [station, stationRows] of byStation) {
    const row = stationRows.find(r => Number(r.hour_start.slice(11, 13)) === currentHour);
    const count = row?.origin_complete && row.origin_count != null
      ? row.origin_count * minuteWeight(minuteInHour, currentHour === peak.get(station)) : null;
    const intensity = count == null || !maxMinute ? 0 : count / maxMinute;
    result[station] = { count, intensity, color: count == null ? "#71717a" : densityColor(intensity) };
  }
  return result;
}
