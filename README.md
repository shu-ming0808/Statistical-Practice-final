# 大稻埕煙火人流分析與捷運班次模擬

## 專案目的

整理北捷逐時 OD、煙火活動日期與社群討論，分析活動人流及車站流向，後續建立回歸預測與班次調整模擬。目前已完成資料前處理、Threads／PTT 文字雲與人流網站第一版，模型與調度模擬尚待建立。

## 快速開始

在專案根目錄執行，各項工作可分別執行：

```powershell
# 安裝環境
uv sync --group notebook --extra social --extra web --extra app

# 人流網站（首次建置見下方「網站啟動與隊員連線」）
.\app\start.ps1

# 捷運資料前處理，並寫入 MySQL
.\.venv\Scripts\python.exe -X utf8 src/main.py --mysql

# 氣象 CSV 驗證、建表及匯入（加 --check-only 只檢查）
.\.venv\Scripts\python.exe -X utf8 src/import_weather.py

# Threads 或 PTT 貼文與文字雲
.\.venv\Scripts\python.exe -X utf8 src/threads_web_collect.py
.\.venv\Scripts\python.exe -X utf8 src/ptt_web_collect.py
```

- **捷運資料**：月 CSV 放在 `data/original_data/`；活動與聲量讀取本機 MySQL，預設 login path 為 `codex-local`。移除 `--mysql` 只省略寫回，仍需讀取資料庫。SQL 檔與資料備份不提交 Git，隊員需另外私下取得並建立資料庫。
- **社群資料**：預設期間為 2026/7/15–8/15。Threads 在新開的 Edge 登入後回終端按 Enter；PTT 不需登入。文字雲位於各平台 `results/` 下的執行資料夾內，檔名為 `wordcloud.png`。
- **氣象資料**：預設讀取 `data/original_data/大稻埕周圍雲量資料.csv`，建立測站、觀測及活動測站對照三表；需先有活動表。相同資料可重跑，PK 內容衝突則整批停止。`23:59` 保留並標記待核對，缺值保留 `NULL`；尚未自動加入回歸表。匯入摘要在 `results/weather/import_summary.json`。

## 專案結構

主要檔案與用途如下：

```text
統計實務/
│
├── README.md                         # 專案介紹與執行方式
├── pyproject.toml                    # uv 環境與套件規格
├── uv.lock                           # 套件鎖定版本
├── .python-version                   # Python 3.10.11
│
├── app/
│   ├── frontend/                     # 黑灰介面、地圖與資料瀏覽
│   ├── backend/                      # FastAPI 與 MySQL 唯讀查詢
│   └── start.ps1                     # 網站啟動／停止
│
├── data/
│   ├── original_data/                # 捷運月 CSV、Threads／PTT 原始樣本
│   ├── interim/                      # 乾淨 OD 與活動來源快照
│   └── processed/                    # 回歸分析表、OD 邊表與節點表
│
├── src/
│   ├── main.py                       # 捷運前處理執行入口
│   ├── load_data.py                  # 原始資料與活動資料讀取
│   ├── data_preprocessing.py         # OD 清理與品質檢查
│   ├── aggregate_flows.py            # 車站人次彙總、回歸表與流向表
│   ├── export_mysql.py               # 分析表寫入 MySQL
│   ├── import_weather.py             # 氣象三表建置、匯入與品質核對
│   ├── threads_web_collect.py        # Threads「最相關」網頁取樣
│   ├── threads_web_report.py         # 共用斷詞、詞頻與文字雲製作
│   ├── ptt_web_collect.py            # PTT 看板標題搜尋與文章取樣
│   └── ptt_web_report.py             # PTT 文字雲執行入口
│
├── mysql/                            # 匯入與 Google Trends 程式；SQL 檔僅保留本機
├── scripts/                          # Threads／PTT PowerShell 啟動腳本
├── notebooks/                        # 資料檢查與分析筆記本
├── tests/                            # 資料清理與網頁取樣測試
├── docs/                             # 操作說明、資料規格、文獻與研究規劃
├── web/meta-review/                  # Meta 審查用隱私與資料刪除網頁
├── 參考資料/                         # 參考文獻原始檔
│
└── results/
    ├── preprocessing/                # 前處理摘要與品質報告
    ├── threads/                      # Threads 詞頻、每日樣本數與文字雲
    ├── ptt/                          # PTT 詞頻、每日樣本數與文字雲
    └── pdf/                          # 活動年表 PDF
```

## 詳細說明與參考資料

- [網站啟動與隊員連線](app/README.md)：本機網址、首次建置與 Tailscale 私人連線。
- [完整資料庫關聯圖](docs/database_schema.dbml)：整份貼到 dbdiagram.io 查看。
- [資料前處理與欄位定義](docs/data_preprocessing.md)
- [Threads 取樣](docs/threads_collection.md)／[PTT 取樣](docs/ptt_collection.md)／[Google Trends](mysql/GOOGLE_TRENDS_README.md)
- [參考論文與專題用途](docs/references.md)：活動人流、圖論、排隊理論與串流處理。
- [研究規劃與驗證方式](docs/research_plan.md)

資料來源：

- [北捷分時進出站運量](https://data.taipei/dataset/detail?id=63f31c7e-7fc3-418b-bd82-b95158755b4d)
- [固定／變動成本](https://www.metro.taipei/cp.aspx?n=20CB8DE3F41B411)：參考 114 年審定決算書。
- [變動／固定公里數](https://whhr.gov.taipei/News_Content.aspx?n=0121E4C78246FC0D&s=3E147DD6A74BE990)
- [天氣資料](https://codis.cwa.gov.tw/StationData)：氣象署 CODiS 氣候觀測資料查詢服務。
- [特殊節假日](https://data.gov.tw/dataset/14718)：政府行政機關辦公日曆表。
- [大稻埕煙火排程參考](https://data.gov.tw/dataset/7778)：觀光資訊活動資料庫。

公開逐時 OD 用於人流分析；逐筆到達與候車時間屬後續模擬。社群文字雲是搜尋樣本的回顧分析，不代表全站聲量。
