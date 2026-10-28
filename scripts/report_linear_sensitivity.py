"""Import/audit teammate result tables and rebuild a report; never fit a model.

Default: python scripts/report_linear_sensitivity.py
Initial import: add --source PATH_TO_TEAMMATE_SHARE_FOLDER
Dependencies: numpy, pandas, matplotlib. No database or private input is required
for rebuilding an existing import. Source predictions are projected to evaluation
outputs and weights; the full feature panel and raw OD are never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import NullFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "result" / "linear_sensitivity"
COPIED = ["factorial_metrics.csv", "factorial_choices.csv", "factorial_tuning.csv",
          "factorial_penalty_path.csv", "factorial_penalty_summary.csv"]
SCALES = {"original": "原縮放", "all_standardized": "全欄標準化"}
MODELS = {"ridge": "重新調參 Ridge", "lasso": "低懲罰 Lasso", "ols": "OLS"}
SCORES = ["mae", "rmse", "peak_abs", "window_under"]
KEYS = ["event_date", "station", "hour"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def close(a, b):
    np.testing.assert_allclose(a, b, rtol=1e-10, atol=1e-8)


def import_results(source):
    (OUT / "source").mkdir(parents=True, exist_ok=True)
    source = source.resolve()
    for name in COPIED:
        shutil.copyfile(source / name, OUT / "source" / name)
    frozen = ROOT / "result" / "forecast" / "tables" / "metrics.csv"
    shutil.copyfile(frozen, OUT / "source" / "frozen_forecast_metrics.csv")
    original = pd.read_csv(source / "factorial_predictions.csv")
    columns = KEYS + ["year", "y"] + [f"{s}_{m}" for s in SCALES for m in MODELS]
    projection = original[columns].copy()
    overlap = ["planned_end_0_60_beimen", "planned_end_60_120_beimen"]
    projection["window_weight_within_event"] = 0.0
    for _, group in original.groupby("event_date"):
        q = group[overlap].to_numpy(float)
        assert np.isfinite(q).all() and (q >= 0).all() and (q.sum(axis=1) <= 1 + 1e-8).all()
        q = q.sum(axis=1)
        assert q.sum() > 0, "This import expects the furnished nine known egress windows"
        projection.loc[group.index, "window_weight_within_event"] = q / q.sum()
    projection.to_csv(OUT / "evaluation_predictions.csv", index=False, encoding="utf-8-sig")
    cfg = json.loads((ROOT / "configs" / "forecast.json").read_text(encoding="utf-8"))
    input_path = ROOT / cfg["input"]
    identity = {"checked": False, "reason": "private frozen input unavailable"}
    if input_path.exists():
        assert sha(input_path) == cfg["input_sha256"]
        panel = pd.read_csv(input_path)
        panel = panel.loc[panel.station.eq("北門") & panel.year.isin([2024, 2025, 2026])]
        checkcols = [c for c in original.columns if c in panel.columns and c not in KEYS]
        left = original.set_index(KEYS).sort_index()
        right = panel.set_index(KEYS).sort_index()
        assert left.index.equals(right.index)
        close(left[checkcols].to_numpy(float), right[checkcols].to_numpy(float))
        identity = {"checked": True, "rows": len(panel), "events": panel.event_date.nunique(),
                    "matching_value_columns": checkcols, "input_sha256": sha(input_path),
                    "private_input_copied": False}
    manifest = {
        "source_package": source.name,
        "import_date": "2026-10-29",
        "status": "teammate result import; no training reproduced",
        "source_file_sha256": {p.name: sha(p) for p in sorted(source.iterdir()) if p.is_file()},
        "copied_source_files": COPIED,
        "frozen_metrics_source": "result/forecast/tables/metrics.csv",
        "frozen_metrics_sha256": sha(frozen),
        "prediction_projection": {
            "source": "factorial_predictions.csv",
            "kept": columns,
            "derived": "window_weight_within_event: sum of two planned overlaps / event overlap sum",
            "excluded": "all feature columns; no training input or raw OD copied",
        },
        "input_identity_at_import": identity,
        "missing_training_scripts": ["src/run_lasso_ridge_experiment.py", "src/run_lasso_ridge_robustness.py"],
    }
    manifest["imported_file_sha256"] = {
        str(p.relative_to(OUT)).replace("\\", "/"): sha(p)
        for p in sorted((OUT / "source").glob("*.csv"))
    }
    manifest["imported_file_sha256"]["evaluation_predictions.csv"] = sha(OUT / "evaluation_predictions.csv")
    write_json(OUT / "provenance.json", manifest)


def score(group, column):
    error = group[column] - group.y
    peak_rows = group.loc[group.groupby("event_date").y.idxmax()]
    under = np.maximum(-error, 0) * group.window_weight_within_event
    return {"mae": abs(error).mean(), "rmse": np.sqrt(np.mean(error ** 2)),
            "peak_abs": abs(peak_rows[column] - peak_rows.y).mean(),
            "window_under": under.groupby(group.event_date).sum().mean()}


def audit(pred, metrics, choices, tuning, path, summary):
    manifest = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    for name, expected in manifest["imported_file_sha256"].items():
        assert sha(OUT / name) == expected, f"Imported source changed: {name}"
    assert len(pred) == 54 and pred.event_date.nunique() == 9
    assert not pred.duplicated(KEYS).any() and pred.station.eq("北門").all()
    for _, group in pred.groupby("event_date"):
        assert set(group.hour) == set(range(18, 24)) and len(group) == 6
        close(group.window_weight_within_event.sum(), 1)
    errors = []
    for row in metrics.itertuples():
        group = pred if str(row.year) == "all" else pred.loc[pred.year.eq(int(row.year))]
        values = score(group, f"{row.scale}_{row.model}")
        for key in SCORES:
            close(values[key], getattr(row, key))
            errors.append(abs(values[key] - getattr(row, key)))
    assert (tuning.validation_year < tuning.outer_year).all()
    assert len(choices) == 12
    for row in choices.itertuples():
        group = tuning.loc[tuning.outer_year.eq(row.year) & tuning.scale.eq(row.scale) & tuning.model.eq(row.model)]
        losses = group.groupby("parameter").apply(
            lambda x: np.average(x.mae, weights=x.validation_events), include_groups=False)
        chosen = losses.loc[np.isclose(losses.index, row.parameter, rtol=1e-10, atol=1e-15)]
        assert len(chosen) == 1
        close(chosen.iloc[0], row.inner_mae)
        close(chosen.iloc[0], losses.min())
    assert len(path) == 126 and len(summary) == 42
    for row in summary.itertuples():
        group = path.loc[path.scale.eq(row.scale) & np.isclose(path.ratio, row.ratio, rtol=1e-10, atol=1e-15)]
        assert len(group) == 3 and set(group.year) == {2024, 2025, 2026}
        for key in ["mae", "bias", "peak_abs", "peak_under", "window_under"]:
            close(group[f"outer_{key}"].mean(), getattr(row, f"lasso_{key}"))
            close(group[f"ridge_outer_{key}"].mean(), getattr(row, f"ridge_{key}"))
        close(np.sqrt(np.mean(group.outer_rmse ** 2)), row.lasso_rmse)
        close(np.sqrt(np.mean(group.ridge_outer_rmse ** 2)), row.ridge_rmse)
        close(group.nonzero_coefficients.mean(), row.mean_nonzero_coefficients)
        assert int(group.selected.sum()) == row.selected_years
    for row in choices.loc[choices.model.eq("lasso")].itertuples():
        selected = path.loc[path.year.eq(row.year) & path.scale.eq(row.scale) & path.selected]
        assert len(selected) == 1
        close(selected.ratio.iloc[0], row.parameter)
        close(selected.actual_alpha.iloc[0], row.fitted_alpha)
        close(selected.nonzero_coefficients.iloc[0], row.nonzero_coefficients)
    result = {"passed": True, "training_reproduced": False,
              "evaluation_rows": len(pred), "evaluation_events": pred.event_date.nunique(),
              "recomputed_metric_rows": len(metrics), "metrics_per_row": SCORES,
              "maximum_absolute_metric_difference": max(errors),
              "annual_choices_checked": len(choices), "tuning_rows_checked": len(tuning),
              "all_validation_years_precede_evaluation_year": True,
              "selected_parameters_minimize_reported_past_event_mae": True,
              "annual_path_rows": len(path), "path_summary_rows_checked": len(summary),
              "limitations": ["training code not furnished", "full path hourly predictions not furnished",
                              "path aggregation verified from annual summaries, not refitted models",
                              "nine events repeatedly inspected; not a new independent test"]}
    write_json(OUT / "validation.json", result)
    return result


def render_figure(summary, metrics):
    available = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in ["Microsoft JhengHei", "Noto Sans CJK TC", "Microsoft YaHei", "Noto Sans CJK SC"] if f in available), None)
    if font is None:
        raise RuntimeError("Install Microsoft JhengHei or Noto Sans CJK TC for Chinese figures")
    plt.rcParams.update({"font.family": [font, "DejaVu Sans"], "axes.unicode_minus": False,
                         "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.4), sharex=True)
    colors = ["#365f80", "#397c68"]
    for i, (scale, label) in enumerate(SCALES.items()):
        group = summary.loc[summary.scale.eq(scale)].sort_values("ratio")
        selected = metrics.loc[metrics.year.eq("all") & metrics.scale.eq(scale)].set_index("model")
        ax = axes[0, i]
        ax.plot(group.ratio, group.lasso_mae, marker="o", ms=3.5, color=colors[i], label="固定比例 Lasso 路徑")
        ax.axhline(selected.loc["ridge", "mae"], color="#555555", ls="--", lw=1.3, label="同程序重新調參 Ridge")
        ax.axhline(selected.loc["ols", "mae"], color="#999999", ls=":", lw=1.5, label="OLS")
        ax.set(title=f"{label}：九場 MAE", ylabel="MAE（人次／來源時段）", ylim=(790, 1840))
        ax.legend(loc="upper left", fontsize=9)
        if scale == "original":
            best = group.loc[group.lasso_mae.idxmin()]
            ax.scatter([best.ratio], [best.lasso_mae], marker="D", s=35, color="#9b4f39", zorder=4)
            ax.annotate("0.0316 → 824.55\n事後最低點，未選用", xy=(best.ratio, best.lasso_mae),
                        xytext=(0.002, 1120), fontsize=9, color="#7e3c2a",
                        arrowprops={"arrowstyle": "->", "color": "#7e3c2a"})
        else:
            ax.annotate("最弱懲罰 846.37 ≈ OLS 846.33\n三年度均由過去資料選到 10⁻⁵",
                        xy=(1e-5, group.iloc[0].lasso_mae), xytext=(2e-5, 1130), fontsize=9,
                        arrowprops={"arrowstyle": "->", "color": colors[i]})
        ax = axes[1, i]
        ax.plot(group.ratio, group.mean_nonzero_coefficients, marker="o", ms=3.5, color=colors[i])
        ax.set(title=f"{label}：非零係數數量", ylabel="三年度平均非零數／36 欄",
               xlabel=r"懲罰比例 $r=\alpha/\alpha_{max}$（向右懲罰越強）", ylim=(-1, 38),
               yticks=[0, 6, 12, 18, 24, 30, 36])
        for ax in axes[:, i]:
            ax.set_xscale("log")
            ax.set_xlim(7e-6, 1.4)
            ax.set_xticks([1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1],
                          ["0.00001", "0.0001", "0.001", "0.01", "0.1", "1"])
            ax.xaxis.set_minor_formatter(NullFormatter())
            ax.grid(axis="y", alpha=0.18)
    fig.suptitle("Lasso 懲罰敏感度｜同九場北門 54 時段；曲線供事後診斷", fontsize=14)
    fig.text(.5, .015, "固定比例仍按訓練折重算 alpha；曲線點不是按年選參的合併成績。非零數是編碼欄位數，並非原始變數數。", ha="center", fontsize=9)
    fig.subplots_adjust(left=.075, right=.98, top=.9, bottom=.1, hspace=.3, wspace=.23)
    (OUT / "figures").mkdir(exist_ok=True)
    fig.savefig(OUT / "figures" / "lasso_penalty_sensitivity.png", dpi=180, facecolor="white")
    plt.close(fig)


def table(frame, label_columns):
    rows = []
    for _, row in frame.iterrows():
        labels = [str(row[c]) for c in label_columns]
        rows.append("| " + " | ".join(labels + [f"{row[c]:,.2f}" for c in SCORES]) + " |")
    return "\n".join(rows)


def report(metrics, choices, summary):
    team = metrics.loc[metrics.year.eq("all")].copy()
    team["scale_label"] = team.scale.map(SCALES)
    team["model_label"] = team.model.map(MODELS)
    frozen = pd.read_csv(OUT / "source" / "frozen_forecast_metrics.csv")
    frozen["scale_label"] = "原專案凍結"
    frozen["model_label"] = frozen.model.map({"ridge": "Ridge", "gam": "GAM", "xgboost": "XGBoost 未加權", "xgboost_weighted": "XGBoost 散場加權（保留）"})
    combined = pd.concat([frozen, team], ignore_index=True)
    combined[["scale_label", "model_label", *SCORES]].to_csv(OUT / "comparison.csv", index=False, encoding="utf-8-sig")
    selected_rows = "\n".join(
        f"| {r.year} | {SCALES[r.scale]} | {MODELS[r.model]} | {r.parameter:.8g} | {r.fitted_alpha:.8g} | {r.nonzero_coefficients}/36 |"
        for r in choices.itertuples())
    ratios = [1, .1, .03162277660168379, .01, .001, .00001]
    path_rows = []
    for ratio in ratios:
        values = []
        for scale in SCALES:
            row = summary.loc[summary.scale.eq(scale) & np.isclose(summary.ratio, ratio, rtol=1e-10, atol=1e-15)].iloc[0]
            values += [f"{row.lasso_mae:,.2f}", f"{row.mean_nonzero_coefficients:.2f}"]
        path_rows.append("| " + f"{ratio:.5g}" + " | " + " | ".join(values) + " |")
    text = f"""# 線性模型與 Lasso 懲罰敏感度補充

本頁匯入隊友 `Lasso_Ridge_實驗分享_2026-10-28` 的結果，於 2026-10-29 核對數值與整理圖表。**本次未重現訓練**：分享包未附 `src/run_lasso_ridge_experiment.py`、`src/run_lasso_ridge_robustness.py`。本次完成的是逐時預測重算、選參表與路徑彙總核對。

核心結論：原縮放下，低懲罰 Lasso 對同程序 Ridge 的 MAE 改善約 **0.315%（2.68 人次）**，散場窗口低估略增；全欄標準化的 Lasso 幾乎等同 OLS。它們均未改變目前保留的 **XGBoost 散場加權**，也不改寫凍結 Ridge 公式。

## 1. 比較範圍與兩種縮放

隊友以相同 21 場四站輸入、相同特徵設計，逐年評估 2024–2026 同九場北門、54 個 18–23 時來源時段；2024／2025／2026 分別用過去 12／15／18 場訓練。匯入時已將隊友評估列的鍵、實際值與共用輸入欄位逐一對齊凍結輸入，並核對其 SHA-256；本目錄不攜帶私有訓練輸入或原始 OD。

- **原縮放：**連續欄標準化，類別編碼維持 0/1。
- **全欄標準化：**再將編碼後的所有欄位標準化。依隊友方法說明，補值、編碼與縮放均只由各訓練折擬合；缺少原始訓練程式，這項實作本次無法直接驗證。
- **同程序重新選參：**Ridge 候選為 λ=0 及 10⁻⁶–10²；Lasso 為 r=alpha/alpha_max 的 21 個比例（10⁻⁵–1），alpha_max 依當次訓練折計算。兩者都用過去驗證活動的北門 MAE 選擇，不能直接比較兩種懲罰參數的數值大小。
- **原專案凍結模型：**沿用既有選參與訓練規則，其成績是同九場的背景對照；不是與隊友重新調參 Ridge 相同的估計程序。

九場曾反覆參與開發，隊友也曾看過九場結果後擴大候選範圍。因此年度先後順序正確仍不足以稱為完全未見資料的驗證；本頁屬探索性回顧比較。

## 2. 四個指標的完整比較

下表數值皆越小越好，單位為人次／來源時段。MAE、RMSE 在九場共 54 列計算；每場都恰有六列。`peak_abs` 是每場**實際**最高人次時段的預測絕對誤差，再對九場取平均。`window_under` 先對每場預定結束後兩小時與來源時段的重疊比例正規化，對正向低估 max(實際−預測, 0) 加權，再平均九場；它不是散場總漏估人數。九場都有可用的預定散場窗口。

| 設定／來源 | 模型 | MAE | RMSE | 尖峰絕對誤差 | 散場窗口低估 |
|---|---|---:|---:|---:|---:|
{table(combined, ['scale_label', 'model_label'])}

原縮放的 Lasso 比重新調參 Ridge 小幅降低 MAE、RMSE、尖峰誤差，但散場窗口低估由 1,188.17 增至 1,202.63；不能說所有指標都改善。全欄標準化的 Lasso MAE 846.37 與 OLS 846.33 僅差約 0.03，且保留 36/36 欄，優勢不能解釋為有效刪除變數。OLS 在兩種縮放下預測一致至浮點誤差。

既有 XGBoost 散場加權在四項指標皆低於這批線性結果，維持原選擇；既有 XGBoost 未加權的窗口低估又低於加權版本，表示即使主模型也存在指標取捨。

## 3. Lasso 懲罰參數敏感度

比例 r 越小，懲罰越弱；比例固定時，各年度實際 alpha 仍不同。下表與圖是**將同一比例套用各年度後的九場路徑**，不等於按年度選到不同比例的合併成績。非零數為三個年度模型的平均，分母是 36 個編碼欄，不能當成原始變數數量。

| 固定 r=alpha/alpha_max | 原縮放 MAE | 原縮放平均非零數 | 全欄標準化 MAE | 全欄標準化平均非零數 |
|---:|---:|---:|---:|---:|
{chr(10).join(path_rows)}

![兩種縮放的 Lasso MAE 與非零係數路徑](figures/lasso_penalty_sensitivity.png)

從 r=0.1 降至 0.01，原縮放 MAE 864.01 → 840.99；再降至 0.001，反而增至 848.36，因此不是懲罰越弱越好。原縮放 r≈0.0316 的 **824.55 是檢視九場後才知道的 MAE 最低點，未被過去資料選到，未採為模型**。該點 RMSE 1,479.58、尖峰絕對誤差 2,457.94，均高於同程序 Ridge（1,330.65、2,315.35）。

## 4. 過去資料實際選到的年度設定

下表 Ridge 的「參數」是 λ，實際 alpha=nλ；Lasso 的「參數」是 r，實際 alpha=r×alpha_max。兩者的目標函數正規化不同；不能把同一欄的數值大小當作兩模型懲罰強度的比較。

| 評估年度 | 縮放 | 模型 | 參數 λ 或 r | 實際 alpha | 非零欄數 |
|---|---|---|---:|---:|---:|
{selected_rows}

原縮放 Lasso 是 2024 選 0.00056234，2025／2026 選 0.001，合併 MAE 才是 **848.52**；全欄標準化三年都選 10⁻⁵，合併 MAE **846.37**。前者不是任一條固定比例曲線的單點。

## 5. 核對範圍、來源與重建

本次由 54 列輸出重算兩種縮放 × 三模型 × 全期間／三年度，共 **24 列的四項指標**；核對 **12 組年度選參**均是已提供過去活動紀錄的 MAE 最小值，**660 列調參紀錄**的驗證年皆早於評估年，且 **126 列年度路徑可彙總回 42 個路徑點**。這些核對證明已提供表格的內部一致性，不證明訓練程式已重跑或完整流程無洩漏。固定路徑未提供逐時預測，所以只驗證其年度到總體的彙總。

來源檔案及保留副本的 SHA-256 見 [provenance.json](provenance.json)，本次檢查結果見 [validation.json](validation.json)。原始分享包逐時檔僅投影保留鍵、真值、六組預測及衍生評分權重於 [evaluation_predictions.csv](evaluation_predictions.csv)，不攜帶完整特徵面板。其餘五份來源 CSV 保留原始位元組。

- [十組模型比較 CSV](comparison.csv)／[來源指標](source/factorial_metrics.csv)／[凍結模型指標快照](source/frozen_forecast_metrics.csv)
- [年度選參](source/factorial_choices.csv)／[過去年度調參紀錄](source/factorial_tuning.csv)
- [完整 42 點敏感度](source/factorial_penalty_summary.csv)／[年度路徑](source/factorial_penalty_path.csv)

從專案根目錄執行 `python scripts/report_linear_sensitivity.py` 可用已保留 CSV 重做數值核對、比較表與圖；需要 numpy、pandas、matplotlib 及可顯示繁體中文的字型。**不需要原下載資料夾、資料庫、私有輸入或模型訓練程式**。此命令不修改 README 或凍結模型檔。
"""
    (OUT / "report.md").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Initial import from the furnished teammate share folder")
    args = parser.parse_args()
    if args.source:
        import_results(args.source)
    read = lambda name: pd.read_csv(OUT / "source" / name)
    pred = pd.read_csv(OUT / "evaluation_predictions.csv")
    metrics, choices, tuning, path, summary = [read(name) for name in COPIED]
    result = audit(pred, metrics, choices, tuning, path, summary)
    render_figure(summary, metrics)
    report(metrics, choices, summary)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
