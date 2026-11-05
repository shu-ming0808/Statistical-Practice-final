"""Package checked forecast and minute-queue evidence for a Canva progress talk."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "result/presentation/method_to_minute_20261103"
DESKTOP = Path.home() / "Desktop/大稻埕_方法至分鐘候車_Canva素材_20261103"
BG, INK, GREEN, ORANGE = "#F4F1E9", "#292E2B", "#668777", "#D89B70"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "Microsoft JhengHei", "font.size": 13,
        "axes.unicode_minus": False, "figure.facecolor": BG,
        "axes.facecolor": BG, "text.color": INK,
        "axes.labelcolor": INK, "xtick.color": INK,
        "ytick.color": INK, "axes.spines.top": False,
        "axes.spines.right": False,
    })
    records: list[dict] = []

    def save(fig, name: str, sources: list[str], note: str) -> None:
        fig.savefig(OUT / name, dpi=180, bbox_inches="tight", transparent=True)
        plt.close(fig)
        records.append({"file": name, "sources": sources, "note": note})

    gam_src = "result/forecast/tables/gam_components_2026.csv"
    gam = pd.read_csv(ROOT / gam_src)
    gam = gam.loc[gam.column == "day_of_year"].sort_values("x_raw")
    assert len(gam) == 80
    fig, ax = plt.subplots(figsize=(11.5, 5.5))
    ax.plot(gam.x_raw, gam.centered_partial_effect, color=GREEN, lw=3)
    ax.axhline(0, color="#9DA39D", lw=1, ls="--")
    ax.set_xlabel("年內日期（day of year）")
    ax.set_ylabel("中心化部分預測貢獻（人次尺度）")
    ax.set_title("GAM 允許年內日期呈現平滑關係", loc="left", fontsize=19, fontweight="bold")
    ax.grid(alpha=.16)
    fig.text(.5, .015, "僅顯示 2026 評估折、截至 2025 訓練的模型曲線；不是因果效果，無信賴區間。", ha="center", fontsize=11)
    fig.tight_layout(rect=(.01, .06, .99, .96))
    save(fig, "P09_GAM年內日期平滑曲線.png", [gam_src], "只畫既有中心化部分預測貢獻，不重新擬合")

    xgb_src = "result/forecast/tables/tree_importance_2026.csv"
    imp = pd.read_csv(ROOT / xgb_src).sort_values("gain_importance", ascending=False).head(8)
    names = {
        "planned_end_0_60_beimen": "北門：預定散場後 0–60 分重疊",
        "planned_end_60_120_beimen": "北門：預定散場後 60–120 分重疊",
        "is_weekend": "週末",
        "day_of_year": "年內日期",
        "retrospective_baseline_56d": "過去 56 日一般日基準",
    }
    labels = [names.get(s, s.replace("hour=", "時段 ") + (" 時" if s.startswith("hour=") else "")) for s in imp.feature]
    fig, ax = plt.subplots(figsize=(11.5, 5.6))
    x = imp.gain_importance.to_numpy()
    ax.barh(np.arange(len(x)), x, height=.58, color=[GREEN] + ["#A7B0A9"] * (len(x)-1))
    ax.set_yticks(np.arange(len(x)), labels)
    ax.invert_yaxis()
    ax.set_xlim(0, max(x) * 1.17)
    ax.set_xlabel("分裂增益重要度（僅本折訓練模型）")
    ax.set_title("XGBoost 主要在哪些欄位分割資料？", loc="left", fontsize=19, fontweight="bold")
    for i, value in enumerate(x):
        ax.text(value + max(x)*.014, i, f"{value:.3f}", va="center", fontsize=11)
    ax.grid(axis="x", alpha=.16)
    ax.set_axisbelow(True)
    fig.text(.5, .015, "重要度不是因果影響，也不保證換一批活動排序相同。", ha="center", fontsize=11)
    fig.tight_layout(rect=(.01, .06, .99, .96))
    save(fig, "P10_XGBoost特徵使用情形.png", [xgb_src], "2026 折訓練重要度，不宣稱因果")

    metrics_src = "result/forecast/tables/metrics.csv"
    metrics = pd.read_csv(ROOT / metrics_src).set_index("model")
    keys = ["ridge", "gam", "xgboost", "xgboost_weighted"]
    labels = ["Ridge", "GAM", "XGBoost 未加權", "XGBoost 散場加權"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.3))
    for ax, metric in zip(axes, ["mae", "rmse"]):
        vals = metrics.loc[keys, metric].to_numpy()
        ax.barh(np.arange(4), vals, color=["#ADB6B0"] * 3 + [GREEN], height=.54)
        ax.set_yticks(np.arange(4), labels)
        ax.invert_yaxis()
        ax.set_xlim(0, max(vals)*1.2)
        ax.set_xlabel("人次／來源時段；越小越好")
        ax.set_title(metric.upper(), loc="left", fontweight="bold")
        ax.grid(axis="x", alpha=.16)
        ax.set_axisbelow(True)
        for i, v in enumerate(vals):
            ax.text(v + max(vals)*.015, i, f"{v:,.2f}", va="center")
    fig.suptitle("九場北門、54 時段：凍結模型的探索性比較", fontsize=18, fontweight="bold")
    fig.tight_layout(rect=(.01, .03, .99, .93), w_pad=3)
    save(fig, "P15_四模型MAE與RMSE.png", [metrics_src], "不混入隊友重新調參程序")

    peak_src = "result/forecast/tables/actual_peaks.csv"
    peaks = pd.read_csv(ROOT / peak_src)
    fig, ax = plt.subplots(figsize=(13, 5.2))
    pos = np.arange(len(peaks))
    ax.bar(pos-.18, peaks.actual, .36, color=INK, label="實際最高時段人次")
    ax.bar(pos+.18, peaks.predicted, .36, color=GREEN, label="該時段預測人次")
    ax.set_xticks(pos, [str(d).replace("-", "/") for d in peaks.event_date], rotation=26, ha="right")
    ax.set_ylabel("人次／來源時段")
    ax.set_ylim(0, 13600)
    ax.grid(axis="y", alpha=.16)
    ax.set_axisbelow(True)
    idx = peaks.index[peaks.event_date == "2024-08-10"][0]
    row = peaks.loc[idx]
    ax.annotate(f"{row.actual:,.0f} → {row.predicted:,.0f}\n少估 {row.actual-row.predicted:,.0f}（{(1-row.predicted/row.actual)*100:.1f}%）",
                xy=(idx, row.actual), xytext=(idx+1, 12200),
                arrowprops={"arrowstyle":"->", "color":ORANGE}, fontsize=12)
    ax.legend(frameon=False, loc="upper right", fontsize=11)
    ax.set_title("平均改善後，實際尖峰仍可能低估", loc="left", fontsize=18, fontweight="bold")
    fig.text(.5,.015,"最高時段是事後評分位置，模型事前不知道真實尖峰在哪一小時。",ha="center",fontsize=11)
    fig.tight_layout(rect=(.01,.06,.99,.96))
    save(fig, "P16_各場最高時段實際與預測.png", [peak_src], "同一個實際最高時段配對比較")

    copies = {
        "P05_變數相關熱力圖.png": "result/forecast/figures/feature_correlations.png",
        "P13_Lasso懲罰路徑.png": "result/linear_sensitivity/figures/lasso_penalty_sensitivity.png",
        "P19_分鐘到達與候車隊伍.png": "result/queue_dispatch_v1/figures/arrival_queue_comparison.png",
        "P20_分鐘情境與剩餘容量.png": "result/queue_dispatch_v1/figures/capacity_sensitivity.png",
        "P21_候車情境區間.png": "result/queue_uncertainty_v1/figures/event_intervals.png",
        "備用_預測誤差分布.png": "result/forecast/figures/prediction_diagnostics.png",
        "備用_九場逐時走勢.png": "result/forecast/figures/hourly_profiles.png",
        "備用_分鐘假設敏感度.png": "result/queue_uncertainty_v1/figures/factor_effects.png",
    }
    for name, source in copies.items():
        shutil.copy2(ROOT / source, OUT / name)
        records.append({"file": name, "sources": [source], "note": "既有圖完整複製，未改數值"})

    for item in records:
        item["sha256"] = hashlib.sha256((OUT/item["file"]).read_bytes()).hexdigest()
        item["source_sha256"] = {s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in item["sources"]}
    (OUT/"manifest.json").write_text(json.dumps({"date":"2026-11-03", "refit":False, "figures":records}, ensure_ascii=False, indent=2), encoding="utf-8")

    DESKTOP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/"docs/canva_method_to_minute_prompt_20261103.txt", DESKTOP/"01_貼給Canva_方法到分鐘候車.txt")
    shutil.copy2(ROOT/"docs/canva_method_to_minute_guide_20261103.md", DESKTOP/"02_逐頁時間與圖片.md")
    shutil.copytree(OUT, DESKTOP/"03_圖片依頁碼", dirs_exist_ok=True)
    old_reference = Path.home()/"Desktop/大稻埕_本次進度報告_新增成果_Canva素材_20261027/版面參考_只沿用設計.pdf"
    if old_reference.exists():
        shutil.copy2(old_reference, DESKTOP/"版面參考_只沿用設計.pdf")
    print(json.dumps({"desktop":str(DESKTOP), "figures":len(records), "refit":False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
