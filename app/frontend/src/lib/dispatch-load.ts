export interface DispatchLoadRow {
  event_date: string;
  plan: "baseline" | "selected_lambda1";
  direction: string;
  departure_time: string;
  within_decision_window: boolean;
  capacity_persons: number;
  remaining_capacity_mean: number;
  remaining_capacity_p05: number;
  remaining_capacity_p95: number;
  arrival_load_mean: number;
  alighting_mean: number;
  preboard_load_mean: number;
  boarded_mean: number;
  departure_load_mean: number;
  queue_left_mean: number;
}

export interface DispatchLoadData {
  schema_version: number;
  evaluation_draws: number;
  rows: DispatchLoadRow[];
  sources: {title: string; url: string}[];
}

export function selectLoadProfile(data: DispatchLoadData, event: string, plan: DispatchLoadRow["plan"], direction: string) {
  return data.rows.filter(r => r.event_date === event && r.plan === plan && r.direction === direction && r.within_decision_window)
    .map(r => {
      const parts = r.departure_time.split(":").map(Number);
      const minute = parts[0] * 60 + parts[1] + (parts[2] ?? 0) / 60;
      const values = [r.remaining_capacity_p05, r.remaining_capacity_mean, r.remaining_capacity_p95];
      if (!Number.isFinite(minute) || minute < 1080 || minute >= 1380 || !(r.capacity_persons > 0) ||
          values.some(v => !Number.isFinite(v) || v < -1e-6 || v > r.capacity_persons + 1e-6) ||
          r.remaining_capacity_p05 > r.remaining_capacity_p95) throw new Error("載量資料超出允許範圍");
      return {...r, minute, meanPercent: r.remaining_capacity_mean / r.capacity_persons * 100,
        rangePercent: [r.remaining_capacity_p05 / r.capacity_persons * 100, r.remaining_capacity_p95 / r.capacity_persons * 100]};
    }).sort((a,b) => a.minute-b.minute);
}
