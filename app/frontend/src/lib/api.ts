export interface EventRecord {
  event_date: string;
  event_name: string;
  event_status: string;
  announced_show_start_time: string | null;
  fireworks_duration_seconds: number | null;
}

export interface FlowRow {
  event_date: string;
  hour_start: string;
  station: string;
  origin_count: number | null;
  destination_count_same_source_bin: number | null;
  origin_complete: boolean | number;
  destination_complete: boolean | number;
  baseline_origin_mean_28d: number | null;
  baseline_n_days: number;
}

export interface FlowResponse {
  rows: FlowRow[];
  metadata: { source: string; time_semantics: string; [key: string]: unknown };
}

export async function api<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal });
  if (!response.ok) {
    let message = "暫時無法讀取資料，請確認本機資料庫與網站服務。";
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* Keep a user-readable message for network and proxy failures. */
    }
    throw new Error(message);
  }
  return response.json();
}

export const numberText = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(
        value,
      );

export const hourOf = (value: string) => Number(value.slice(11, 13));

/** A missing or incomplete source bin must not silently become zero. */
export function completeTotal(
  rows: FlowRow[],
  field: "origin_count" | "destination_count_same_source_bin",
  expected = 6,
): number | null {
  const complete =
    field === "origin_count" ? "origin_complete" : "destination_complete";
  if (
    rows.length !== expected ||
    rows.some((row) => row[field] == null || !row[complete])
  )
    return null;
  return rows.reduce((sum, row) => sum + (row[field] as number), 0);
}

export function csvUrl(date: string, stations: string[]) {
  return `/api/flows.csv?${new URLSearchParams({ event_date: date, stations: stations.join(","), start_hour: "18", end_hour: "24" })}`;
}
