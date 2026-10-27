export interface DispatchComparison {
  event_date: string;
  waiting_weight: number;
  baseline_services: number;
  optimized_services: number;
  added: number;
  removed: number;
  retimed: number;
  baseline_operating_twd: number;
  optimized_operating_twd: number;
  operating_change_twd: number;
  baseline_mean_wait: number;
  optimized_mean_wait: number;
  baseline_p95: number | null;
  optimized_p95: number | null;
  wait_change_percent: number;
  unserved_mean: number;
  baseline_unserved_mean: number;
  evaluation_total_cost_change: number;
}
export interface StationImpact {
  event_date: string;
  waiting_weight: number;
  station: string;
  passengers: number;
  baseline_mean_wait: number | null;
  optimized_mean_wait: number | null;
  baseline_green_wait: number | null;
  optimized_green_wait: number | null;
  unaffected_bypass_passengers: number;
}
export interface TimetableChange {
  event_date: string;
  waiting_weight: number;
  direction: string;
  action: string;
  old_beimen: string;
  new_beimen: string;
  origin: string;
  old_origin: string;
  new_origin: string;
}
export type DispatchSummary = Pick<DispatchComparison,
  "waiting_weight" | "baseline_services" | "optimized_services" | "added" | "removed" |
  "retimed" | "baseline_operating_twd" | "optimized_operating_twd" | "operating_change_twd" |
  "baseline_mean_wait" | "optimized_mean_wait" | "wait_change_percent" | "evaluation_total_cost_change"
> & {events: number};
export interface DispatchData {
  schema_version: number;
  generated_at: string;
  decision_window: string;
  waiting_weights: number[];
  assumptions: string[];
  comparison: DispatchComparison[];
  station_impacts: StationImpact[];
  timetable: TimetableChange[];
  summary: DispatchSummary[];
  sources: {title: string; url: string}[];
  no_wait_threshold: boolean;
  not_global_optimum: boolean;
}

export function selectDispatch(data: DispatchData, event: string, weight: number) {
  const matches = (r: {event_date: string; waiting_weight: number}) =>
    r.event_date === event && r.waiting_weight === weight;
  return {
    comparison: data.comparison.find(matches),
    stations: data.station_impacts.filter(r => matches(r) && r.station !== "其他旅次"),
    timetable: data.timetable.filter(matches),
  };
}
