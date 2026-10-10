"""Report explicitly assumed directional queues from held-out flow predictions.

This report compares hypothetical remaining capacities and headways. It does
not estimate observed waiting times, certify platform safety, or optimize an
operational timetable. M/M/c is intentionally not used for train service.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter, MultipleLocator
import numpy as np
import pandas as pd

from .queueing import compare_headway_scenarios, disaggregate_hourly


def _chinese_font() -> str:
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/msjh.ttf")):
        if path.is_file():
            return font_manager.FontProperties(fname=str(path)).get_name()
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans CJK SC", "SimHei", "Arial Unicode MS"):
        if name in available:
            return name
    raise RuntimeError("A Chinese font is required to render the queue scenario report")


def _clock_label(value: float, _position: int) -> str:
    total_minutes = int(round(value * 60))
    day, remainder = divmod(total_minutes, 24 * 60)
    hour, minute = divmod(remainder, 60)
    return f"{hour:02d}:{minute:02d}" + (f"\n+{day}日" if day else "")


def _clearance_metrics(arrivals: list[float], metrics: dict[str, Any]) -> tuple[float, float | None, float | None]:
    positive_ends = [index + 1 for index, count in enumerate(arrivals) if count > 0]
    last_arrival = float(max(positive_ends, default=0))
    if not metrics["cleared"]:
        return last_arrival, None, None
    if not positive_ends:
        return last_arrival, 0.0, 0.0
    tolerance = 1e-9 * max(1.0, metrics["total_arrivals"])
    for row in metrics["trace"]:
        if row["time_minutes"] >= last_arrival and row["queue_after_service"] <= tolerance:
            clear_time = float(row["time_minutes"])
            return last_arrival, clear_time, clear_time - last_arrival
    raise RuntimeError("Queue marked cleared but no clearance boundary was recorded")


def build_queue_report(predictions: pd.DataFrame, config: dict[str, Any], output: Path) -> dict[str, Any]:
    """Write figures/queue_scenarios.png and tables/queue_scenarios.csv.

    ``output`` is the analysis output root (normally result/analysis_v1).
    ``config['queue_example']`` fixes event_date, station, forecast_model,
    direction_shares, walk_delay_minutes and explicit scenario configurations.
    Hourly predictions must be unique, nonnegative, finite and consecutive;
    the function never fills missing hours with zero or chooses another event.
    """
    example = config["queue_example"]
    model = example["forecast_model"]
    required = {"event_date", "station", "hour", model}
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"Queue report missing prediction columns: {sorted(missing)}")
    event = pd.Timestamp(example["event_date"]).normalize()
    dates = pd.to_datetime(predictions["event_date"], errors="raise").dt.normalize()
    chosen = predictions.loc[(dates == event) & (predictions["station"] == example["station"])].copy()
    if chosen.empty:
        raise ValueError(f"No held-out predictions for queue example {event.date()} / {example['station']}")
    numeric_hours = pd.to_numeric(chosen["hour"], errors="raise").to_numpy(float)
    if (not np.isfinite(numeric_hours).all() or not np.equal(numeric_hours, np.floor(numeric_hours)).all()
            or (numeric_hours < 0).any() or (numeric_hours > 23).any()):
        raise ValueError("Queue example hours must be integers from 0 to 23")
    chosen["hour"] = numeric_hours.astype(int)
    chosen = chosen.sort_values("hour")
    hours = chosen["hour"].tolist()
    if hours != list(range(hours[0], hours[-1] + 1)):
        raise ValueError("Queue example requires unique consecutive hourly predictions; do not zero-fill gaps")
    hourly = pd.to_numeric(chosen[model], errors="raise").to_numpy(float)
    if not np.isfinite(hourly).all() or (hourly < 0).any():
        raise ValueError("Queue report requires finite nonnegative flow predictions")
    shares = example["direction_shares"]
    if len(shares) != 2:
        raise ValueError("This report expects exactly two hypothetical directions")
    minute_arrivals = disaggregate_hourly(
        hourly, shares, walk_delay_minutes=example["walk_delay_minutes"],
    )
    allocated = math.fsum(math.fsum(values) for values in minute_arrivals.values())
    predicted = math.fsum(hourly)
    if not math.isclose(allocated, predicted, rel_tol=1e-9, abs_tol=1e-8):
        raise RuntimeError("Hourly-to-minute allocation did not preserve predicted demand")
    source_scope = config.get("evaluation_kind", "held_out_predictions_information_availability_unspecified")
    assumptions = [
        "使用所指定活動、車站與模型的留出預測，並非實測分鐘到達。",
        "方向比例為明示假設；每小時內均勻到達、初始隊列為零。",
        f"閘門至月台採固定 {example['walk_delay_minutes']} 分鐘延遲，保留跨午夜人流。",
        "列車瞬間批次登車；各班剩餘容量由情境給定，未另推估轉乘或上游載客。",
        "均值人流的流體模擬不等於隨機佇列的期望候車時間。",
        "未驗證班表、車隊、號誌、月台安全與乘務限制；不是營運建議，未做調度最佳化。",
    ]
    comparisons = {
        direction: compare_headway_scenarios(values, example["scenarios"])
        for direction, values in minute_arrivals.items()
    }
    if not example["scenarios"]:
        raise ValueError("Queue report requires at least one explicit headway scenario")
    records: list[dict[str, Any]] = []
    for direction, cases in comparisons.items():
        for case in cases:
            metrics, scenario = case["metrics"], case["config"]
            last_arrival, clear_time, clear_after = _clearance_metrics(minute_arrivals[direction], metrics)
            records.append({
                "event_date": event.date().isoformat(), "station": example["station"],
                "forecast_model": model, "evaluation_kind": source_scope,
                "direction": direction, "direction_share": float(shares[direction]),
                "scenario": case["scenario_name"],
                "headway_minutes": scenario["headway_minutes"],
                "remaining_capacity_per_train": scenario["available_capacity"],
                "first_train_minutes": scenario["first_train_minutes"],
                "walk_delay_minutes": int(example["walk_delay_minutes"]),
                "clearance_window_minutes": scenario["clearance_minutes"],
                "total_forecast_arrivals": metrics["total_arrivals"],
                "total_boarded": metrics["total_boarded"],
                "waiting_person_minutes": metrics["waiting_person_minutes"],
                "mean_wait_minutes": metrics["mean_wait_minutes"],
                "max_waiting_queue": metrics["max_queue"],
                "residual_queue": metrics["residual_queue"], "cleared": metrics["cleared"],
                "last_positive_arrival_minutes": last_arrival,
                "clearance_time_from_start_minutes": clear_time,
                "clearance_after_last_arrival_minutes": clear_after,
                "arrival_horizon_minutes": metrics["arrival_horizon_minutes"],
                "simulation_horizon_minutes": metrics["horizon_minutes"],
                "train_count": metrics["train_count"],
                "trains_boarding_passengers": sum(row["boarded"] > 0 for row in metrics["train_records"]),
                "capacity_caused_carryover": any(row["left_behind"] > 1e-8 for row in metrics["train_records"]),
                "waiting_space_limit": metrics["waiting_space_limit"],
                "waiting_space_exceeded": metrics["waiting_space_exceeded"],
                "conservation_error": metrics["conservation_error"],
                "assumptions": "；".join(assumptions + case["assumptions"]),
            })
    no_carryover = all(not row["capacity_caused_carryover"] for row in records)
    half_headway = all(row["mean_wait_minutes"] is not None and math.isclose(
        row["mean_wait_minutes"], row["headway_minutes"] / 2, rel_tol=1e-9, abs_tol=1e-9)
        for row in records)
    if no_carryover and half_headway:
        interpretation = (
            "本例各情境均未因容量不足而滯留，平均等待恰為班距的一半。這是均勻分鐘到達、"
            "足夠剩餘容量與本例時段配置造成的結果，不能證明實際煙火散場尖峰沒有壅塞。"
        )
    else:
        interpretation = (
            "隊列與等待取決於所設方向比例、均勻分鐘到達、剩餘容量及班距；"
            "這些條件式結果不能直接推論實際煙火尖峰是否壅塞。"
        )
    output = Path(output)
    figure_path = output / "figures" / "queue_scenarios.png"
    table_path = output / "tables" / "queue_scenarios.csv"
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.parent.mkdir(parents=True, exist_ok=True)
    font = _chinese_font()
    with plt.rc_context({"font.family": [font, "DejaVu Sans"], "axes.unicode_minus": False,
                         "font.size": 10, "axes.spines.top": False, "axes.spines.right": False}):
        figure, axes = plt.subplots(2, 1, figsize=(12, 8.4), sharex=True)
        try:
            colors = ("#202020", "#666666", "#969696")
            styles = ("-", "--", ":")
            for axis, (direction, cases) in zip(axes, comparisons.items()):
                for index, case in enumerate(cases):
                    x, y = [], []
                    for row in case["metrics"]["trace"]:
                        time = hours[0] + row["time_minutes"] / 60.0
                        x.extend((time, time))
                        y.extend((row["queue_before_service"], row["queue_after_service"]))
                    axis.plot(x, y, color=colors[index % len(colors)],
                              linestyle=styles[index % len(styles)], linewidth=1.5,
                              label=case["scenario_name"])
                axis.set_title(f"{direction}｜假設占 {100 * float(shares[direction]):g}%", loc="left", fontsize=12)
                axis.set_ylabel("候車人數（情境模擬）")
                axis.set_ylim(bottom=0)
                axis.grid(axis="y", color="#dedede", linewidth=0.6)
                axis.legend(loc="upper right", frameon=False, ncol=min(3, len(cases)))
                axis.xaxis.set_major_locator(MultipleLocator(1))
                axis.xaxis.set_major_formatter(FuncFormatter(_clock_label))
            axes[-1].set_xlabel("時間（跨午夜續算，包含額外清空服務期間）")
            figure.suptitle(f"{example['station']}候車情境｜{event.date().isoformat()}｜{model} 留出預測", fontsize=15, y=0.97)
            capacity_text = "/".join(f"{float(case['available_capacity']):g}" for case in example["scenarios"])
            if len({float(case["available_capacity"]) for case in example["scenarios"]}) == 1:
                capacity_text = f"{float(example['scenarios'][0]['available_capacity']):g}"
            figure.text(0.5, 0.927, f"每小時內均勻到達；步行延遲 {example['walk_delay_minutes']} 分鐘；每班剩餘容量假設 {capacity_text} 人",
                        ha="center", fontsize=10)
            figure.text(0.08, 0.026, "假設比較，非北捷實測候車或營運建議。未驗證完整月台安全、車隊及班表限制；未做調度最佳化。",
                        fontsize=9, color="#555555")
            figure.subplots_adjust(left=0.08, right=0.98, bottom=0.12, top=0.875, hspace=0.27)
            figure.savefig(figure_path, dpi=180, facecolor="white")
        finally:
            plt.close(figure)
    pd.DataFrame(records).to_csv(table_path, index=False, encoding="utf-8-sig")
    return {
        "status": "created_hypothetical_queue_scenario_report",
        "event_date": event.date().isoformat(), "station": example["station"],
        "forecast_model": model, "evaluation_kind": source_scope,
        "source_hours": hours, "source_hourly_predictions": [float(v) for v in hourly],
        "total_predicted_entries": predicted, "total_allocated_arrivals": allocated,
        "directions": dict(shares), "walk_delay_minutes": int(example["walk_delay_minutes"]),
        "figure": figure_path.relative_to(output).as_posix(),
        "table": table_path.relative_to(output).as_posix(),
        "assumptions": assumptions, "scenarios": records,
        "interpretation": interpretation,
        "all_scenarios_without_capacity_carryover": no_carryover,
        "all_scenario_mean_waits_equal_half_headway": half_headway,
        "operational_optimization_performed": False,
        "actual_platform_safety_verified": False,
    }
