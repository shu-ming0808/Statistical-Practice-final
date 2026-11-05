"""Additional sensitivity checks for the frozen minute-weight scale experiment."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from experiment_minute_weight_scale import ROOT, SCALES, load_predictions, predictions, scored

sys.path.insert(0, str(ROOT / "src"))
from queue_scenarios import shaped_arrivals  # noqa: E402
from queueing import simulate_bulk_queue  # noqa: E402


DISPLAY_SCALES = (1.0, 1.05, 1.10, 1.20)
SHAPES = {"broad": 20, "concentrated": 5}
FRACTIONS = (0.25, 0.50)


def event_effects(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for event_date, group in frame.groupby("event_date"):
        group = group.reset_index(drop=True)
        baseline = scored(group, predictions(group, 1.0))["hourly_mae"]
        peak = group.loc[group.forecast_peak].iloc[0]
        for scale in DISPLAY_SCALES:
            new_mae = scored(group, predictions(group, scale))["hourly_mae"]
            rows.append({"event_date": event_date, "year": int(event_date[:4]),
                         "forecast_peak_hour": int(peak.hour), "scale": scale,
                         "extra_predicted_people": float((scale - 1) * peak.frozen_w4),
                         "baseline_hourly_mae": baseline, "scaled_hourly_mae": new_mae,
                         "mae_change": new_mae - baseline})
    return pd.DataFrame(rows)


def chronological_check(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for test_year in (2025, 2026):
        training = frame.loc[frame.event_date.str[:4].astype(int).lt(test_year)].reset_index(drop=True)
        test = frame.loc[frame.event_date.str[:4].astype(int).eq(test_year)].reset_index(drop=True)
        train_scores = [scored(training, predictions(training, scale))["hourly_mae"] for scale in SCALES]
        scale = float(SCALES[np.argmin(train_scores)])
        rows.append({"test_year": test_year, "training_events": training.event_date.nunique(),
                     "test_events": test.event_date.nunique(), "past_selected_scale": scale,
                     "baseline_test_mae": scored(test, predictions(test, 1.0))["hourly_mae"],
                     "scaled_test_mae": scored(test, predictions(test, scale))["hourly_mae"]})
    result = pd.DataFrame(rows)
    result["test_mae_change"] = result.scaled_test_mae - result.baseline_test_mae
    return result


def asymmetric_costs(frame: pd.DataFrame) -> pd.DataFrame:
    truth = frame.y.to_numpy(float)
    rows = []
    for under_cost in (1, 2, 3):
        values = []
        for scale in SCALES:
            error = predictions(frame, scale) - truth
            cost = np.mean(under_cost * np.maximum(-error, 0) + np.maximum(error, 0))
            values.append(cost)
        best = int(np.argmin(values))
        for scale in DISPLAY_SCALES:
            position = int(np.flatnonzero(np.isclose(SCALES, scale))[0])
            rows.append({"underestimate_cost_ratio": under_cost, "scale": scale,
                         "mean_asymmetric_error": float(values[position]),
                         "best_scale_for_ratio": float(SCALES[best]),
                         "best_mean_asymmetric_error": float(values[best])})
    return pd.DataFrame(rows)


def underprediction_breakdown(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep one-sided shortfalls separate from signed bias and absolute error."""
    truth = frame.y.to_numpy(float)
    forecast_peak_mask = frame.forecast_peak.to_numpy(bool)
    actual_peak_indices = frame.groupby("event_date").y.idxmax().to_numpy()
    rows = []
    for scale in (1.0, 1.03, 1.04, 1.05, 1.06, 1.07, 1.08, 1.09,
                  1.10, 1.11, 1.12, 1.20, 1.33, 1.50, 2.0, 3.0):
        estimate = predictions(frame, scale)
        under = np.maximum(truth - estimate, 0)
        over = np.maximum(estimate - truth, 0)
        rows.append({"scale": scale, "observed_total": float(truth.sum()),
                     "predicted_total": float(estimate.sum()),
                     "net_actual_minus_predicted": float((truth - estimate).sum()),
                     "hourly_under_total": float(under.sum()),
                     "hourly_over_total": float(over.sum()),
                     "under_at_forecast_peak_hours": float(under[forecast_peak_mask].sum()),
                     "under_at_unchanged_hours": float(under[~forecast_peak_mask].sum()),
                     "actual_peak_under_mean": float(under[actual_peak_indices].mean()),
                     "actual_peak_under_total": float(under[actual_peak_indices].sum())})
    event_rows = []
    for event_date, group in frame.groupby("event_date"):
        forecast_peak = group.loc[group.forecast_peak].iloc[0]
        actual_peak = group.loc[group.y.idxmax()]
        same_hour = bool(forecast_peak.hour == actual_peak.hour)
        event_rows.append({"event_date": event_date,
                           "forecast_peak_hour": int(forecast_peak.hour),
                           "actual_peak_hour": int(actual_peak.hour),
                           "same_peak_hour": same_hour,
                           "actual_at_forecast_peak": float(forecast_peak.y),
                           "prediction_at_forecast_peak": float(forecast_peak.frozen_w4),
                           "forecast_peak_shortfall_zero_at_k": float(forecast_peak.y / forecast_peak.frozen_w4),
                           "actual_peak_observed": float(actual_peak.y),
                           "prediction_at_actual_peak": float(actual_peak.frozen_w4),
                           "actual_peak_under_k1": float(max(actual_peak.y - actual_peak.frozen_w4, 0)),
                           "actual_peak_under_k105": float(max(actual_peak.y - actual_peak.frozen_w4 * (1.05 if same_hour else 1), 0)),
                           "actual_peak_under_k150": float(max(actual_peak.y - actual_peak.frozen_w4 * (1.5 if same_hour else 1), 0))})
    breakdown = pd.DataFrame(rows)
    if not np.allclose(breakdown.hourly_under_total - breakdown.hourly_over_total,
                       breakdown.net_actual_minus_predicted, atol=1e-7):
        raise AssertionError("Net gap must equal underprediction minus overprediction")
    if not np.allclose(breakdown.under_at_unchanged_hours,
                       breakdown.under_at_unchanged_hours.iloc[0], atol=1e-7):
        raise AssertionError("Untouched hours changed across scales")
    if (np.diff(breakdown.hourly_under_total) > 1e-7).any() or (np.diff(breakdown.actual_peak_under_mean) > 1e-7).any():
        raise AssertionError("One-sided underprediction increased when scale increased")
    if not np.isclose(breakdown.actual_peak_under_mean.iloc[0], 658.0101182725695, atol=1e-6):
        raise AssertionError("Published actual-peak underprediction does not match")
    return breakdown, pd.DataFrame(event_rows)


def underprediction_explanation(breakdown: pd.DataFrame, events: pd.DataFrame) -> list[str]:
    baseline = breakdown.loc[breakdown.scale.eq(1.0)].iloc[0]
    at_105 = breakdown.loc[breakdown.scale.eq(1.05)].iloc[0]
    plateau = breakdown.loc[breakdown.scale.eq(1.5)].iloc[0]
    august = events.loc[events.event_date.eq("2024-08-10")].iloc[0]
    mismatched_under = events.loc[~events.same_peak_hour & events.actual_peak_under_k1.gt(0)]
    matched_under = events.loc[events.same_peak_hour & events.actual_peak_under_k1.gt(0)].copy()
    matched_under["zero_at_k"] = matched_under.actual_peak_observed / matched_under.prediction_at_actual_peak
    matched_under = matched_under.sort_values("zero_at_k")
    largest_crossing = events.forecast_peak_shortfall_zero_at_k.max()
    total_peak_forecast = events.prediction_at_forecast_peak.sum()
    lines = [
        "## 為什麼倍率增加會讓『少預測的人數』下降", "",
        "### 1. 先定義本實驗改變了什麼", "",
        "令活動 `e` 的時段 `h` 實際人次為 `y[e,h]`、原封存預測為 `ŷ[e,h]`。`p[e]` 是**由原預測選出的最高小時**，`a[e]` 是事後由實際人數找出的最高小時。兩者可能不同。對 `k ≥ 1`，實驗的新預測為：", "",
        "```text",
        "新預測[e,h;k] = k × ŷ[e,h]    若 h = p[e]",
        "               = ŷ[e,h]        若 h ≠ p[e]",
        "```", "",
        "原分鐘權重 `w[m]` 的 60 個值合計為 1。改成 `k × w[m]` 後合計為 `k`，所以該小時的 60 個分鐘人次合計也從 `ŷ[e,p]` 變成 `k × ŷ[e,p]`。這是**增加預測的總人次**，並非只把同一批人移到不同分鐘。σ=5 或 20 分鐘只改分鐘形狀；對以下逐時低估數字沒有影響。", "",
        "### 2. 三種容易混淆的『少預測』", "",
        "本報告的單位為**人次**。逐時人次相加不一定代表不重複的個別乘客。令 `[x]₊ = max(x, 0)`：", "",
        "1. **逐時低估總量** `U(k) = Σ[e,h] [y[e,h] − 新預測[e,h;k]]₊`。每個小時只累計正的缺口；高估小時記 0。", "2. **總量淨差額** `N(k) = Σ[e,h] (y[e,h] − 新預測[e,h;k])`。高估會抵銷低估；負值表示總預測已大於總實際。", "3. **實際尖峰平均低估** `A(k) = (1/9) Σ[e] [y[e,a[e]] − 新預測[e,a[e];k]]₊`。每場只看*實際尖峰所在的小時*，並對九場平均；這就是前表的「實際尖峰低估」。", "",
        "這三個量的分母與處理高估的方式不同，不能互相替代。`U` 是 54 個小時的低估總人次；`A` 是九場實際尖峰的平均低估人次；`N` 則容許正負誤差互相抵銷。若另外把逐時高估總量寫成 `O(k)=Σ[e,h][新預測−y]₊`，則有恆等式 **`N(k)=U(k)−O(k)`**，而 **`小時 MAE=(U(k)+O(k))/54`**。", "",
        "### 3. 逐時低估為什麼單調不增加", "",
        "對被放大的某場預測尖峰小時，低估是 `[y[e,p] − k×ŷ[e,p]]₊`。由於 `ŷ[e,p] ≥ 0`，增加 `k` 只會把括號內的值降低。若原本已高估，該項一直是 0；若原本低估，它以每增加 1 倍減少 `ŷ[e,p]` 人次的速度下降，直到 `k = y[e,p]/ŷ[e,p]` 時歸零，其後維持 0。其餘五個小時完全不變，因此總和 `U(k)` **單調不增加**，但不保證每增加一點都嚴格下降。", "",
        f"九場預測尖峰小時的 `y/ŷ` 最大為 **{largest_crossing:.6f}**。因此在本實驗的 0.01 格點上，從 **k=1.33** 起，九個被放大的小時都不再低估；其他 45 個小時沒被放大，仍可保留低估。", "",
        "### 4. 總量淨差額為什麼持續下降", "",
        f"九場被放大的原預測人次合計是 **{total_peak_forecast:,.2f}**。因此 `N(k) = N(1) − (k−1)×{total_peak_forecast:,.2f}`。這條關係是線性的，即使 `U(k)` 已降到平台，淨差額仍會下降，最後變負。負值表示整體變成高估，不能解讀為『低估改善得更好』。", "",
        "| k | 54 小時低估總量 U | 其中：被放大小時低估 | 其中：未變小時低估 | 54 小時高估總量 | 總量淨差額 N | 實際尖峰平均低估 A |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in breakdown.itertuples():
        lines.append(f"| {row.scale:.2f} | {row.hourly_under_total:,.2f} | {row.under_at_forecast_peak_hours:,.2f} | {row.under_at_unchanged_hours:,.2f} | {row.hourly_over_total:,.2f} | {row.net_actual_minus_predicted:,.2f} | {row.actual_peak_under_mean:,.2f} |")
    lines += ["", f"例如 `k=1.05` 時，相對原設定，54 小時低估總量從 **{baseline.hourly_under_total:,.2f}** 降到 **{at_105.hourly_under_total:,.2f}**，但高估總量也從 **{baseline.hourly_over_total:,.2f}** 升到 **{at_105.hourly_over_total:,.2f}**。到 `k=1.50`，逐時低估仍有 **{plateau.hourly_under_total:,.2f}** 人次，來自未改動的小時；再放大同一個尖峰小時也消不掉它。", "",
              "### 5. 實際尖峰低估為什麼下降，最後又停住", "",
              "若 `a[e] = p[e]`，實際尖峰的低估是 `[y[e,a] − k×ŷ[e,a]]₊`，隨 `k` 增加下降到 0。若 `a[e] ≠ p[e]`，實驗改的是另一個小時；實際尖峰小時的預測仍是 `ŷ[e,a]`，低估完全不動。把九場平均後，`A(k)` 也只能單調不增加，並可能留下一個大於 0 的平台。", "",
              "| 活動日期 | 預測尖峰時 | 實際尖峰時 | 是否同時段 | 實際尖峰人次 | 原本該時段預測 | k=1 低估 | k=1.05 低估 | k=1.50 低估 |",
              "|---|---:|---:|:---:|---:|---:|---:|---:|---:|"]
    for row in events.itertuples():
        lines.append(f"| {row.event_date} | {row.forecast_peak_hour} | {row.actual_peak_hour} | {'是' if row.same_peak_hour else '否'} | {row.actual_peak_observed:,.0f} | {row.prediction_at_actual_peak:,.2f} | {row.actual_peak_under_k1:,.2f} | {row.actual_peak_under_k105:,.2f} | {row.actual_peak_under_k150:,.2f} |")
    lines += ["", "把九場拆開，實際尖峰低估的函數可寫成 `(同時段活動的 [y−kŷ]₊ 之和 + 不同時段活動的固定低估之和) / 9`。本批資料中，原本在實際尖峰低估且兩種尖峰同時段的活動，依低估歸零的倍率排序如下：", "",
              "| 活動日期 | 低估歸零的 k = 實際／原預測 |",
              "|---|---:|"]
    for row in matched_under.itertuples():
        lines.append(f"| {row.event_date} | {row.zero_at_k:.6f} |")
    lines += ["", "倍率每跨過一個臨界點，就少一場會繼續受益；到最後一個臨界點後，實際尖峰低估停在不同時段活動留下的平台。", "",
              f"具體例子：2024-08-10 的預測與實際尖峰都在 {int(august.actual_peak_hour)} 時，實際 **{august.actual_peak_observed:,.0f}** 人次，原預測 **{august.prediction_at_actual_peak:,.2f}** 人次。`k=1` 低估 **{august.actual_peak_under_k1:,.2f}**；`k=1.05` 時預測變成 **{1.05*august.prediction_at_actual_peak:,.2f}**，低估變 **{august.actual_peak_under_k105:,.2f}**。到約 `k={august.actual_peak_observed/august.prediction_at_actual_peak:.6f}` 時低估歸零；繼續增加就改為高估。", ""]
    if len(mismatched_under):
        parts = [f"{row.event_date}：{row.actual_peak_under_k1:,.2f}" for row in mismatched_under.itertuples()]
        lines += [f"仍有低估且尖峰小時不一致的活動是 **{'；'.join(parts)}** 人次。它們的實際尖峰小時沒有被放大，合計 **{mismatched_under.actual_peak_under_k1.sum():,.2f}**，除以九場後就是平台 **{mismatched_under.actual_peak_under_k1.sum()/9:,.2f} 人次／場**。", ""]
    lines += ["### 6. 為什麼下降不等於預測變準", "",
              "低估是**單邊指標**：只看 `[實際−預測]₊`。把預測任意放大，這個指標自然下降或持平；即使預測已超過實際很多，它仍記為 0。相對地，高估 `[預測−實際]₊` 會增加。逐時 MAE 等於 `(逐時低估總量 + 逐時高估總量) / 54`，所以 MAE 可能先小幅下降、再因高估快速增加。前一份報告的資料內 MAE 在 `k=1.05` 最低，留一場選倍率的 MAE 則比原設定更高。", "",
              "此外，本實驗只放大*預測*尖峰小時，不會移動預測尖峰到正確時段。實際尖峰若落在另一小時，其缺口不會因為同一小時倍率更大而消失。這也是實際尖峰低估最後不降到 0 的原因。", "",
              "這些單調關係是公式本身的數學結果，並非發現了乘客一定更多、分鐘分布更合理，或候車預測更準。九場資料亦曾參與原模型開發；新活動的倍率仍需要獨立資料驗證。", "",
              "完整計算見 `underprediction_breakdown.csv` 與 `actual_peak_breakdown.csv`。", ""]
    return lines


def queue_effects(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tables = pd.read_csv(ROOT / "result/queue_dispatch_v1/metro_timetable.csv")
    departures = pd.read_csv(ROOT / "result/queue_dispatch_v1/metro_departure.csv")
    capacity = pd.read_csv(ROOT / "result/queue_dispatch_v1/metro_capacity_reference.csv")
    beimentables = tables.loc[tables.station_label.eq("北門")]
    if len(beimentables) != 2 or float(capacity.max_train_persons.iloc[0]) != 2220:
        raise ValueError("Expected two Beimen directions and published train capacity")
    rows = []
    for event_date, group in frame.groupby("event_date"):
        counts = group.sort_values("hour").frozen_w4.to_numpy(float)
        weekday = date.fromisoformat(event_date).weekday()
        if weekday not in (2, 5):
            raise ValueError("Event day has no explicit service policy")
        day_type = "weekday" if weekday == 2 else "saturday"
        for scale in DISPLAY_SCALES:
            scaled = counts.copy()
            scaled[int(np.argmax(counts))] *= scale
            for shape, sigma in SHAPES.items():
                arrivals = shaped_arrivals(scaled, shape, sigma, center=30, delay=5)
                for fraction in FRACTIONS:
                    simulations = []
                    for table in beimentables.itertuples():
                        rows_for_train = departures.loc[
                            departures.source_id.eq(table.source_id)
                            & departures.day_type.eq(day_type)
                            & departures.departure_minute.ge(1080)]
                        train_times = sorted((rows_for_train.departure_minute - 1080).tolist())
                        simulations.append(simulate_bulk_queue(
                            arrivals * 0.5, train_times,
                            [2220 * fraction] * len(train_times)))
                    total_demand = sum(q["total_arrivals"] for q in simulations)
                    rows.append({"event_date": event_date, "scale": scale, "shape": shape,
                                 "remaining_fraction": fraction, "forecast_demand": total_demand,
                                 "mean_wait_minutes": (sum(q["waiting_person_minutes"] for q in simulations) / total_demand
                                                       if all(q["cleared"] for q in simulations) else np.nan),
                                 "max_direction_queue": max(q["max_queue"] for q in simulations),
                                 "residual_queue": sum(q["residual_queue"] for q in simulations),
                                 "cleared": all(q["cleared"] for q in simulations)})
    detail = pd.DataFrame(rows)
    summary = detail.groupby(["scale", "shape", "remaining_fraction"], as_index=False).agg(
        mean_wait_minutes=("mean_wait_minutes", "mean"),
        worst_direction_queue=("max_direction_queue", "max"),
        max_residual_queue=("residual_queue", "max"),
        mean_forecast_demand=("forecast_demand", "mean"),
        all_cleared=("cleared", "all"))
    old = pd.read_csv(ROOT / "result/queue_dispatch_v1/summary.csv")
    for row in summary.loc[summary.scale.eq(1.0)].itertuples():
        match = old.loc[old["shape"].eq(row.shape) & old.remaining_fraction.eq(row.remaining_fraction)]
        if len(match) != 1 or not np.isclose(match.mean_wait_minutes.iloc[0], row.mean_wait_minutes, atol=1e-7):
            raise AssertionError("Queue baseline does not reproduce the published scenario")
    return detail, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prediction_csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "result/minute_weight_scale_experiment")
    args = parser.parse_args()
    frame = load_predictions(args.prediction_csv)
    events = event_effects(frame)
    chronological = chronological_check(frame)
    asymmetric = asymmetric_costs(frame)
    under_breakdown, actual_peaks = underprediction_breakdown(frame)
    queue_detail, queue_summary = queue_effects(frame)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (("event_effects", events), ("chronological_check", chronological),
                       ("asymmetric_costs", asymmetric), ("queue_effects", queue_detail),
                       ("queue_summary", queue_summary),
                       ("underprediction_breakdown", under_breakdown),
                       ("actual_peak_breakdown", actual_peaks)):
        data.to_csv(args.output / f"{name}.csv", index=False, encoding="utf-8-sig")
    lines = ["# 倍率增加的補充分析", "",
             "延續 `report.md`：只放大每場預測最高的小時，其他小時不變。逐時準確度以封存預測和實際人數計算；候車數字是現行一般班表與容量假設下的壓力情境。", ""]
    lines += underprediction_explanation(under_breakdown, actual_peaks)
    lines += ["## 逐場差異", "",
             "| 倍率 | MAE 改善場數 | MAE 變差場數 | 每場平均額外預測人數 |",
             "|---:|---:|---:|---:|"]
    for scale in DISPLAY_SCALES[1:]:
        part = events.loc[events.scale.eq(scale)]
        lines.append(f"| {scale:.2f} | {int(part.mae_change.lt(0).sum())} | {int(part.mae_change.gt(0).sum())} | {part.extra_predicted_people.mean():.0f} |")
    lines += ["", "各場細節見 `event_effects.csv`。額外預測人數是需求放大量，不代表實際新增乘客。", "",
              "## 按年份向前選倍率", "",
              "每年只用更早年份的活動選小時 MAE 最低倍率，再測試該年。", "",
              "| 測試年 | 可用過去場數 | 過去選出的倍率 | 原 MAE | 放大後 MAE | 變化 |",
              "|---:|---:|---:|---:|---:|---:|"]
    for row in chronological.itertuples():
        lines.append(f"| {row.test_year} | {row.training_events} | {row.past_selected_scale:.2f} | {row.baseline_test_mae:.2f} | {row.scaled_test_mae:.2f} | {row.test_mae_change:+.2f} |")
    lines += ["", "## 低估與高估的取捨", "",
              "以所有 54 個小時的平均非對稱誤差比較；高估成本固定為 1，低估成本分別設為 1、2、3。成本比例是情境設定，不是估計出的實際損失。", "",
              "| 低估成本比例 | 此成本下的最佳倍率 | 最低平均成本 | k=1 平均成本 |",
              "|---:|---:|---:|---:|"]
    for ratio, part in asymmetric.groupby("underestimate_cost_ratio"):
        base = part.loc[part.scale.eq(1.0)].iloc[0]
        lines.append(f"| {ratio}:1 | {base.best_scale_for_ratio:.2f} | {base.best_mean_asymmetric_error:.2f} | {base.mean_asymmetric_error:.2f} |")
    lines += ["", "## 候車壓力情境", "",
              "沿用北門現行一般班表、雙向各 50%、步行延遲 5 分鐘、尖峰中心第 30 分鐘。比較 σ=20／5 分及每班剩餘空間 25%／50%；這些不是歷史實測候車。", "",
              "| 倍率 | σ（分） | 剩餘空間 | 九場平均候車（分） | 最擁擠方向最大候車人數 |",
              "|---:|---:|---:|---:|---:|"]
    for row in queue_summary.itertuples():
        lines.append(f"| {row.scale:.2f} | {SHAPES[row.shape]} | {row.remaining_fraction:.0%} | {row.mean_wait_minutes:.2f} | {row.worst_direction_queue:.0f} |")
    lines += ["", "候車表只顯示需求放大對壓力的影響；平均候車越低不代表預測越準。若有未清空的場次，完整平均候車不報數值，並在 `queue_effects.csv` 留下殘留人數。", "",
              "逐時倍率仍只來自九場曾參與原模型開發的活動。逐年檢查的訓練場數尤其少，不能據此宣稱新活動的最佳倍率。", ""]
    (args.output / "effects.md").write_text("\n".join(lines), encoding="utf-8")
    print(chronological.round(2).to_string(index=False))
    print(asymmetric.loc[asymmetric.scale.eq(1.0)].round(2).to_string(index=False))
    print(queue_summary.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
