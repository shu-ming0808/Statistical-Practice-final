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
  candidate_family?: string;
  headway_valid?: boolean;
  min_changed_headway_minutes?: number | null;
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
export interface DispatchSlotCount {
  event_date: string;
  waiting_weight: number;
  direction: "往松山" | "往新店";
  slot_start: string;
  slot_end: string;
  baseline_count: number;
  optimized_count: number;
  net_change: number;
}
export interface DispatchFamilyComparison {
  event_date: string;
  waiting_weight: number;
  original_candidate: string;
  expanded_candidate: string;
  original_family: string;
  expanded_family: string;
  original_added: number;
  expanded_added: number;
  original_removed: number;
  expanded_removed: number;
  original_retimed: number;
  expanded_retimed: number;
  original_services: number;
  expanded_services: number;
  original_operating_change_twd: number;
  expanded_operating_change_twd: number;
  original_mean_wait: number;
  expanded_mean_wait: number;
  original_wait_person_minutes: number;
  expanded_wait_person_minutes: number;
  original_unserved_mean: number;
  expanded_unserved_mean: number;
  original_max_train_load: number;
  expanded_max_train_load: number;
  original_min_changed_headway_minutes: number | null;
  expanded_min_changed_headway_minutes: number | null;
  original_train_objective: number;
  expanded_train_objective: number;
  original_evaluation_total_cost_change: number;
  expanded_evaluation_total_cost_change: number;
  selected_by_training: boolean;
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
  slot_counts: DispatchSlotCount[];
  candidate_family_comparison?: DispatchFamilyComparison[];
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
    slots: (data.slot_counts ?? []).filter(matches),
    candidateComparison: data.candidate_family_comparison?.find(matches),
  };
}

export function dispatchSlotLabel(slot: DispatchSlotCount) {
  const change = slot.net_change > 0 ? `增加 ${slot.net_change}` :
    slot.net_change < 0 ? `減少 ${Math.abs(slot.net_change)}` : "不變，0";
  return `${slot.direction}，${slot.slot_start} 至 ${slot.slot_end}，原班表 ${slot.baseline_count} 班，新班表 ${slot.optimized_count} 班，時段淨變化 ${change} 班`;
}
