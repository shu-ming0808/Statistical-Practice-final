"""Retrospective Beimen queue stress test, holding all non-demand assumptions fixed.

Run: .venv/Scripts/python.exe -X utf8 scripts/report_queue_replay.py
Observed hourly OD counts are used only after the event for evaluation.
"""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from queue_scenarios import shaped_arrivals  # noqa: E402
from queueing import simulate_bulk_queue  # noqa: E402

OUT = ROOT / "result" / "queue_replay"
SHAPES = {"uniform": None, "broad": 20, "concentrated": 5}
FRACTIONS = (0.25, 0.50, 0.75)
SOURCE = "2026-08-30 general timetable counterfactual, not historical activity service"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def paired_arrivals(predicted: np.ndarray, observed: np.ndarray, shape: str, sigma: int | None, delay: int) -> tuple[np.ndarray, np.ndarray]:
    """Use the forecast-chosen peak hour and identical minute weights for both paths."""
    pred = shaped_arrivals(predicted, shape, sigma, center=30, delay=delay)
    hourly_profile = pred[delay:].reshape(6, 60) / predicted[:, None]
    actual = np.r_[np.zeros(delay), (observed[:, None] * hourly_profile).ravel()]
    if not np.allclose(actual[delay:].reshape(6, 60).sum(axis=1), observed):
        raise AssertionError("Observed hourly mass changed during disaggregation")
    return pred, actual


def replay() -> list[dict]:
    cfg = json.loads((ROOT / "configs/queue_dispatch_v1.json").read_text(encoding="utf-8"))
    source = ROOT / cfg["prediction_path"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != cfg["prediction_sha256"]:
        raise ValueError("Frozen forecast checksum mismatch")
    forecast = read_csv(source)
    if len(forecast) != 54 or {r["station"] for r in forecast} != {"北門"}:
        raise ValueError("Expected 54 Beimen forecast rows")
    times = read_csv(ROOT / "result/queue_dispatch_v1/metro_timetable.csv")
    departures = read_csv(ROOT / "result/queue_dispatch_v1/metro_departure.csv")
    beimen = [t for t in times if t["station_label"] == "北門" and t["line_code"] == "G"]
    if len(beimen) != 2:
        raise ValueError("Expected two Beimen green-line directions")
    by_event: dict[str, list[dict[str, str]]] = {}
    for row in forecast:
        by_event.setdefault(row["event_date"], []).append(row)
    if len(by_event) != 9:
        raise ValueError("Expected nine held-out event dates")
    result = []
    for event, event_rows in sorted(by_event.items()):
        event_rows.sort(key=lambda r: int(r["hour"]))
        if [int(r["hour"]) for r in event_rows] != list(range(18, 24)):
            raise ValueError(f"Incomplete six-hour event: {event}")
        predicted = np.array([float(r[cfg["prediction_column"]]) for r in event_rows])
        observed = np.array([float(r["y"]) for r in event_rows])
        if np.min(predicted) <= 0 or np.min(observed) < 0:
            raise ValueError("Invalid forecast or observed count")
        weekday = date.fromisoformat(event).weekday()
        if weekday not in (2, 5):
            raise ValueError("Event requires explicit service day mapping")
        day_type = "weekday" if weekday == 2 else "saturday"
        for shape, sigma in SHAPES.items():
            pred_arr, obs_arr = paired_arrivals(predicted, observed, shape, sigma, cfg["platform_delay_minutes"])
            for fraction in FRACTIONS:
                for table in beimen:
                    north = "松山" in table["direction_text"]
                    direction = "往松山" if north else "往新店／台電大樓"
                    selected = [d for d in departures if d["source_id"] == table["source_id"]
                                and d["day_type"] == day_type and int(d["departure_minute"]) >= 1080]
                    if not selected or any(d["terminal"] != ("松山" if north else "新店") for d in selected):
                        raise ValueError("Incompatible or missing evening train service")
                    train_times = sorted(int(d["departure_minute"]) - 1080 for d in selected)
                    capacities = [2220 * fraction] * len(train_times)
                    before = simulate_bulk_queue(pred_arr * 0.5, train_times, capacities)
                    after = simulate_bulk_queue(obs_arr * 0.5, train_times, capacities)
                    for label, queue in (("forecast", before), ("observed_hindsight", after)):
                        if abs(queue["conservation_error"]) > 1e-5:
                            raise AssertionError("Queue mass conservation failed")
                        result.append(dict(event_date=event, day_type=day_type, shape=shape,
                                           remaining_fraction=fraction, direction=direction, demand_source=label,
                                           total_demand_18_23=round(queue["total_arrivals"], 4),
                                           waiting_person_minutes=round(queue["waiting_person_minutes"], 4),
                                           mean_wait_minutes="" if queue["mean_wait_minutes"] is None else round(queue["mean_wait_minutes"], 4),
                                           max_queue=round(queue["max_queue"], 4),
                                           residual_queue=round(queue["residual_queue"], 4),
                                           cleared=queue["cleared"], train_count=queue["train_count"]))
    if len(result) != 324:
        raise AssertionError("Unexpected scenario count")
    return result


def output(rows: list[dict]) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "paired_scenarios.csv", rows)
    pairs = {}
    for row in rows:
        key = tuple(row[k] for k in ("event_date", "shape", "remaining_fraction", "direction"))
        pairs.setdefault(key, {})[row["demand_source"]] = row
    comparisons = []
    for (event, shape, fraction, direction), pair in sorted(pairs.items()):
        predicted, actual = pair["forecast"], pair["observed_hindsight"]
        comparisons.append(dict(event_date=event, shape=shape, remaining_fraction=fraction, direction=direction,
                                predicted_demand=predicted["total_demand_18_23"], observed_demand=actual["total_demand_18_23"],
                                predicted_wait_person_minutes=predicted["waiting_person_minutes"],
                                hindsight_wait_person_minutes=actual["waiting_person_minutes"],
                                wait_person_minutes_difference=round(actual["waiting_person_minutes"] - predicted["waiting_person_minutes"], 4),
                                predicted_max_queue=predicted["max_queue"], hindsight_max_queue=actual["max_queue"],
                                max_queue_difference=round(actual["max_queue"] - predicted["max_queue"], 4),
                                predicted_residual=predicted["residual_queue"], hindsight_residual=actual["residual_queue"],
                                both_cleared=predicted["cleared"] and actual["cleared"]))
    write_csv(OUT / "comparison.csv", comparisons)
    selected = [r for r in comparisons if r["shape"] == "broad" and r["remaining_fraction"] == 0.5]
    events = []
    for event in sorted({r["event_date"] for r in selected}):
        subset = [r for r in selected if r["event_date"] == event]
        predicted_wait = round(sum(r["predicted_wait_person_minutes"] for r in subset))
        hindsight_wait = round(sum(r["hindsight_wait_person_minutes"] for r in subset))
        events.append(dict(event_date=event,
                           predicted_demand=round(sum(r["predicted_demand"] for r in subset)),
                           observed_demand=round(sum(r["observed_demand"] for r in subset)),
                           predicted_wait_person_minutes=predicted_wait,
                           hindsight_wait_person_minutes=hindsight_wait,
                           wait_person_minutes_difference=hindsight_wait - predicted_wait,
                           predicted_max_queue=round(max(r["predicted_max_queue"] for r in subset)),
                           hindsight_max_queue=round(max(r["hindsight_max_queue"] for r in subset)),
                           both_cleared=all(r["both_cleared"] for r in subset)))
    write_csv(OUT / "event_summary.csv", events)
    plt.rcParams.update({"font.family": "Microsoft JhengHei", "axes.unicode_minus": False})
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    xx = np.arange(len(events))
    ax.bar(xx - 0.18, [e["predicted_wait_person_minutes"] for e in events], width=0.36, label="事前預測輸入", color="#777f84")
    ax.bar(xx + 0.18, [e["hindsight_wait_person_minutes"] for e in events], width=0.36, label="事後實際逐時輸入", color="#c17e5e")
    ax.set_xticks(xx, [e["event_date"][2:].replace("-", "/") for e in events], rotation=40, ha="right")
    ax.set_ylabel("雙向候車人分鐘（模型情境）")
    ax.set_title("九場北門：同班表、同分鐘形狀與容量，只更換逐時人數")
    ax.legend(); ax.grid(axis="y", alpha=0.2)
    fig.savefig(OUT / "forecast_vs_hindsight_wait.png", dpi=180, transparent=True)
    plt.close(fig)
    sensitivities = []
    for event in sorted({r["event_date"] for r in comparisons}):
        for shape in SHAPES:
            for fraction in FRACTIONS:
                subset = [r for r in comparisons if r["event_date"] == event and r["shape"] == shape
                          and r["remaining_fraction"] == fraction]
                sensitivities.append(dict(event_date=event, shape=shape, remaining_fraction=fraction,
                                          predicted_wait_person_minutes=round(sum(r["predicted_wait_person_minutes"] for r in subset)),
                                          hindsight_wait_person_minutes=round(sum(r["hindsight_wait_person_minutes"] for r in subset)),
                                          both_cleared=all(r["both_cleared"] for r in subset)))
    summary = {"schema_version": 1, "events": events, "sensitivity": sensitivities, "assumptions": {"shape": "broad", "peak_sigma_minutes": 20,
               "remaining_capacity_fraction": 0.5, "northbound_share": 0.5,
               "platform_delay_minutes": 5, "timetable": SOURCE,
               "actual_is_hindsight": True, "minute_counts_observed": False,
               "train_load_observed": False, "historical_queue_reconstruction": False}}
    (OUT / "public_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# 預測誤差與北門候車情境的回顧檢查", "",
              "只把封存預測換成事後實際逐時 OD 人數；日期、兩方向班表、分鐘分布形狀、方向 50:50、每班剩餘容量及 5 分鐘站內延遲都固定。實際逐時人數只作事後檢查，不是活動前一天的輸入。",
              "", "中心展示：預測最高小時內採寬峰 σ=20 分；每班剩餘容量 1,110 人。完整 3 種形狀 × 3 種容量 × 2 方向見 CSV。", "",
              "| 日期 | 預測人次 | 實際人次 | 預測情境候車人分鐘 | 事後情境候車人分鐘 | 差額 |", "|---|---:|---:|---:|---:|---:|"]
    for e in events:
        report.append(f"| {e['event_date']} | {e['predicted_demand']:,} | {e['observed_demand']:,} | {e['predicted_wait_person_minutes']:,} | {e['hindsight_wait_person_minutes']:,} | {e['wait_person_minutes_difference']:+,} |")
    report += ["", "![事前預測與事後逐時人數下的候車情境](forecast_vs_hindsight_wait.png)", "",
               "正差額表示在相同假設下，實際人數造成的模擬等待多於事前預測。這不是實測候車時間，也不是全線班表調度的回測；未觀測分鐘到達、方向比例與逐班載量。使用 2026/8/30 一般班表反事實重播 2024–2026 活動，不能代表活動當日實際班表。", "",
               "九場已反覆檢視，這是模型診斷而非新活動的獨立驗證。若要確認調度方案在預測誤差下的可靠度，下一步須用觀測逐班載量與活動實際班表重播全線。", "",
               "[逐組結果](paired_scenarios.csv) · [差額](comparison.csv) · [逐場摘要](event_summary.csv)"]
    (OUT / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    data = output(replay())
    print(json.dumps({"events": len(data["events"]), "output": str(OUT)}, ensure_ascii=False))
