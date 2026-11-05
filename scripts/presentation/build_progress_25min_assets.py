"""Build Canva-ready figures from published results; never refit or select models."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "result/presentation/progress_20261029"
BG, INK, GREEN, ORANGE = "#F4F1E9", "#292E2B", "#668777", "#D89B70"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-dir", type=Path)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Microsoft JhengHei", "font.size": 13,
                         "axes.unicode_minus": False, "figure.facecolor": BG,
                         "axes.facecolor": BG, "text.color": INK,
                         "axes.labelcolor": INK, "xtick.color": INK,
                         "ytick.color": INK, "axes.spines.top": False,
                         "axes.spines.right": False})
    records = []

    def save(fig, name, sources, note):
        fig.savefig(OUT / name, dpi=180, transparent=True)
        plt.close(fig)
        records.append({"file": name, "sources": sources, "note": note})

    metrics_path = "result/forecast/tables/metrics.csv"
    metrics = pd.read_csv(ROOT / metrics_path).set_index("model")
    keys = ["ridge", "gam", "xgboost", "xgboost_weighted"]
    labels = ["Ridge", "GAM", "XGBoost 未加權", "XGBoost 散場加權"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.1))
    for ax, metric in zip(axes, ["mae", "rmse"]):
        values = metrics.loc[keys, metric].to_numpy()
        ax.barh(np.arange(4), values, color=["#ADB6B0"] * 3 + [GREEN], height=.53)
        ax.set_yticks(np.arange(4), labels)
        ax.invert_yaxis()
        ax.set_xlim(0, max(values) * 1.22)
        ax.set_xlabel("人次／來源時段；越低越好")
        ax.set_title(metric.upper(), loc="left", fontweight="bold")
        ax.grid(axis="x", alpha=.16)
        ax.set_axisbelow(True)
        for i, value in enumerate(values):
            ax.text(value + max(values)*.018, i, f"{value:,.2f}", va="center")
    fig.suptitle("同九場北門、54 時段｜原專案凍結模型", fontsize=18, fontweight="bold")
    fig.text(.5, .025, "探索性回顧比較；不含隊友重新調參的線性模型。", ha="center", fontsize=11)
    fig.tight_layout(rect=(.01,.07,.99,.92), w_pad=3)
    save(fig, "P09_凍結模型_MAE與RMSE.png", [metrics_path], "原模型指標；非新增模型實驗")

    peak_path = "result/forecast/tables/actual_peaks.csv"
    peaks = pd.read_csv(ROOT / peak_path)
    fig, ax = plt.subplots(figsize=(13, 5.2))
    pos = np.arange(len(peaks))
    ax.bar(pos-.18, peaks.actual, .36, color=INK, label="實際尖峰時段人次")
    ax.bar(pos+.18, peaks.predicted, .36, color=GREEN, label="同一時段的模型預測")
    ax.set_xticks(pos, [str(d).replace("-", "/") for d in peaks.event_date], rotation=28, ha="right")
    ax.set_ylabel("人次／來源時段")
    ax.set_ylim(0, 13600)
    ax.grid(axis="y", alpha=.16)
    ax.set_axisbelow(True)
    idx = peaks.index[peaks.event_date == "2024-08-10"][0]
    row = peaks.loc[idx]
    ax.annotate(f"{row.actual:,.0f} → {row.predicted:,.0f}\n少估 {row.actual-row.predicted:,.0f}（{(1-row.predicted/row.actual)*100:.1f}%）",
                xy=(idx, row.actual), xytext=(idx+1, 12200),
                arrowprops={"arrowstyle":"->", "color":ORANGE}, fontsize=13)
    ax.legend(frameon=False, loc="upper right", fontsize=11)
    ax.set_title("平均誤差下降，仍要保留尖峰風險", loc="left", fontsize=18, fontweight="bold")
    fig.text(.5,.018,"實際最高時段為事後評分位置；不是模型事前已知的尖峰。",ha="center",fontsize=11)
    fig.tight_layout(rect=(.01,.08,.99,.97))
    save(fig, "P10_每場實際尖峰與預測.png", [peak_path], "同一實際尖峰時段配對比較；非各自最大值")

    slots_path = "result/green_dispatch/slot_counts.csv"
    slots = pd.read_csv(ROOT / slots_path)
    selected = slots[(slots.event_date == "2024-07-31") & (slots.waiting_weight == 1)]
    directions = ["往松山", "往新店"]
    times = sorted(selected.slot_start.unique())
    data = selected.pivot(index="direction",columns="slot_start",values="net_change").loc[directions,times]
    assert data.shape == (2,20) and not data.isna().any().any()
    cmap = LinearSegmentedColormap.from_list("dispatch", ["#527B91", BG, "#B1662E"])
    fig, ax = plt.subplots(figsize=(13,4.3))
    extent = max(1, abs(data.to_numpy()).max())
    im = ax.imshow(data.to_numpy(),cmap=cmap,norm=TwoSlopeNorm(vmin=-extent,vcenter=0,vmax=extent),aspect="auto")
    ax.set_yticks([0,1],directions)
    ax.set_xticks(np.arange(20),times,rotation=45,ha="right",fontsize=11)
    ax.set_xlabel("北門離站時段起點；每格 15 分鐘，23:00 不在調整窗內")
    for y in range(2):
        for x in range(20):
            v = int(data.iloc[y,x])
            ax.text(x,y,f"{v:+d}" if v else "0",ha="center",va="center",fontsize=13,
                    color="white" if abs(v) >= extent*.9 else INK)
    ax.set_title("2024/07/31，λ=1｜格內班數淨變化：新班表－原班表",loc="left",fontsize=17,fontweight="bold")
    fig.colorbar(im,ax=ax,orientation="horizontal",pad=.4,fraction=.06,shrink=.7,label="藍：較少　米白：相同　橘：較多")
    fig.text(.5,.025,"本場實際新增 50、取消 0、移時 87；藍格也可能只是移到相鄰時段。",ha="center",fontsize=11)
    fig.tight_layout(rect=(.01,.05,.99,.97))
    save(fig,"P14_單場15分鐘班數熱圖.png",[slots_path,"result/green_dispatch/comparison.csv"],"節錄 2024-07-31，λ=1；未修改班表")

    trade_path = "result/green_dispatch_diagnostics/tradeoff_summary.csv"
    trade = pd.read_csv(ROOT/trade_path).set_index("scenario_id")
    fig, ax = plt.subplots(figsize=(12.5,6))
    offsets = {"baseline":(14,15),"current_0.5":(15,18),"current_1":(10,18),
               "current_2":(-95,18),"fixed_8":(14,15),"fixed_10":(16,0)}
    for key,row in trade.iterrows():
        diag = key.startswith("fixed")
        color = ORANGE if diag else INK if key=="baseline" else GREEN
        x,y=row.operating_change_twd/10000,row.mean_wait
        ax.scatter(x,y,s=110,marker="D" if diag else "o",color=color,zorder=3)
        label=row.label + ("（診斷）" if diag else "")
        ax.annotate(f"{label}\n{x:+,.1f} 萬／{y:.2f} 分",(x,y),xytext=offsets[key],
                    textcoords="offset points",fontsize=12)
    ax.axvline(0,color="#9BA19B",ls="--",lw=1)
    ax.set_xlim(-125,570)
    ax.set_ylim(2,9)
    ax.set_xlabel("相對原班表的營運支出差（萬元）；左側省錢，右側增支")
    ax.set_ylabel("乘客加權平均候車（分鐘）；越低越好")
    ax.set_title("八場合計｜省費用與縮短等待的取捨",loc="left",fontsize=19,fontweight="bold")
    ax.grid(alpha=.16)
    fig.text(.5,.025,"包含一般背景與可推導轉乘旅次；8／10 分配置僅供診斷。離散候選點不是已證明的最優前緣。",ha="center",fontsize=11)
    fig.tight_layout(rect=(.01,.07,.99,.97))
    save(fig,"P15_八場成本候車取捨.png",[trade_path],"只重繪既有 300 次評估彙總；不據評估重新選方案")

    copies = {
        "P08_Lasso懲罰敏感度.png":"result/linear_sensitivity/figures/lasso_penalty_sensitivity.png",
        "P12_逐班剩餘容量.png":"result/green_dispatch_diagnostics/figures/beimen_remaining_capacity.png",
        "備用_特徵相關熱力圖.png":"result/forecast/figures/feature_correlations.png",
        "備用_預測誤差分布.png":"result/forecast/figures/prediction_diagnostics.png",
        "備用_四站轉乘影響.png":"result/green_dispatch/figures/station_impacts.png",
    }
    for name,src in copies.items():
        shutil.copy2(ROOT/src,OUT/name)
        records.append({"file":name,"sources":[src],"note":"既有圖完整複製，未改數值"})
    for record in records:
        record["sha256"] = hashlib.sha256((OUT/record["file"]).read_bytes()).hexdigest()
        record["source_sha256"] = {src:hashlib.sha256((ROOT/src).read_bytes()).hexdigest() for src in record["sources"]}
    (OUT/"manifest.json").write_text(json.dumps({"date":"2026-10-29","refit":False,"figures":records},ensure_ascii=False,indent=2),encoding="utf-8")
    if args.desktop_dir:
        desktop = args.desktop_dir.resolve()
        desktop.mkdir(parents=True,exist_ok=True)
        for filename, dest in [("canva_progress_25min_prompt.txt","01_貼給Canva_25分鐘.txt"),
                               ("canva_progress_25min_guide.md","02_逐頁時間與圖片對照.md")]:
            shutil.copy2(ROOT/"docs"/filename,desktop/dest)
        shutil.copytree(OUT,desktop/"03_圖片依頁碼",dirs_exist_ok=True)
    print(json.dumps({"output":str(OUT),"figures":len(records),"desktop":str(args.desktop_dir)},ensure_ascii=False))


if __name__ == "__main__":
    main()
