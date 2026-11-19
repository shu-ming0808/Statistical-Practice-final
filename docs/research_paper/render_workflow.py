"""Render the editable workflow.mmd stages into an A4-friendly PDF figure."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib import font_manager


HERE = Path(__file__).resolve().parent
font_path = Path(r"C:\Windows\Fonts\msjh.ttc")
font_manager.fontManager.addfont(str(font_path))
font_name = font_manager.FontProperties(fname=str(font_path)).get_name()

labels_zh = [
    "原始資料\n逐時 OD、活動、班表、日曆",
    "資料清理與時間標記",
    "活動前一日資訊界線",
    "按年度向前回測",
    "Ridge、Lasso、GAM\n與 XGBoost 比較",
    "封存四站 XGBoost\n逐時預測",
    "分鐘到站情境\n均勻、寬峰、集中峰",
    "列車載客守恆\n與候車計算",
    "18–23 時\n增班、減班、移時候選",
    "訓練情境選案\n獨立情境評估",
    "成本與候車取捨\n限制與不確定性",
]
labels_en = [
    "Hourly OD, event schedules,\ncalendars and timetables",
    "Validate keys and time stamps",
    "Define the day-before\ninformation set",
    "Forward-year\nforecast evaluation",
    "Simple benchmarks, linear\nmodels, GAM and XGBoost",
    "Freeze the four-station\nhourly demand forecast",
    "Minute-arrival and\ncapacity scenarios",
    "Train-by-train flow\nconservation and waiting",
    "18:00–23:00 timetable:\nadd, cancel and retime",
    "Select on training scenarios;\ntest on independent draws",
    "Cost–waiting trade-off\nand validity audit",
]

for labels, filename, size, height, top, row_step, box_height in (
    (labels_zh, "workflow.pdf", 9.5, 7.6, 6.35, 1.12, .66),
    (labels_en, "workflow_en.pdf", 8.2, 4.8, 4.15, .72, .49),
):
    fig, ax = plt.subplots(figsize=(7.3, height))
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 7.3)
    ax.set_ylim(0, height)
    ax.axis("off")
    positions = []
    box_width = 2.8 if filename == "workflow_en.pdf" else 2.48
    for i in range(len(labels)):
        row, col = divmod(i, 2)
        if row % 2:
            col = 1 - col
        x, y = (0.35 if filename == "workflow_en.pdf" else 0.5) + col * 3.8, top - row * row_step
        positions.append((x, y))
        ax.add_patch(Rectangle((x, y), box_width, box_height, linewidth=0.8,
                               edgecolor="#262626", facecolor="white"))
        ax.text(x + box_width / 2, y + box_height / 2, labels[i], ha="center", va="center",
                fontsize=size, fontname=font_name, color="#111111")
    for (x1, y1), (x2, y2) in zip(positions[:-1], positions[1:]):
        if abs(y1 - y2) < 0.01:
            a = (x1 + box_width + 0.04, y1 + box_height / 2) if x2 > x1 else (x1 - 0.04, y1 + box_height / 2)
            b = (x2 - 0.08, y2 + box_height / 2) if x2 > x1 else (x2 + box_width + 0.08, y2 + box_height / 2)
        else:
            a = (x1 + box_width / 2, y1 - 0.04)
            b = (x2 + box_width / 2, y2 + box_height + 0.04)
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=11,
                                     linewidth=0.8, color="#333333"))
    fig.savefig(HERE / filename, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
