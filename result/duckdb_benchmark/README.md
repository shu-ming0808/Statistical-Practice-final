# 為何本專案使用 DuckDB：人流資料實測

測試完成時間：2026-10-20 16:54（臺灣時間）。本文件的數字來自實際執行，不是官方效能宣傳或推估。

## 1. 結論與實測耗時

本機這組實驗支持「大量 OD 分析交給 DuckDB，關聯資料仍由 MySQL 管理」的分工。
DuckDB 的優勢出現在多站／全月彙總；精準的北門單日查詢則是有索引的 MySQL 較快。
不能把大型彙總的加速倍數套用到所有 SQL，更不能解讀為模型預測因此比較準。

以下為 **暖機 2 次後、重複 7 次的中位數，單位毫秒（ms），越小越快**。
MySQL 已建立正確型別、主鍵，以及分別支援篩選和分組的三個涵蓋索引；
日期、時段及人次的轉型在準備階段完成，查詢階段使用已清理的欄位。

| 相同查詢工作 | MySQL／含分組索引 | DuckDB／1 執行緒、128 MiB | DuckDB／4 執行緒、2 GB | 四執行緒版比較 |
|---|---:|---:|---:|---|
| 北門單日 18–23 時逐時人次 | 0.727 | 3.690 | 2.949 | MySQL 約 4.1 倍 |
| 四站全月晚間逐日逐時人次 | 103.220 | 51.776 | 17.136 | DuckDB 約 6.0 倍 |
| 全站全月依車站與時段彙總 | 3092.604 | 103.940 | 39.110 | DuckDB 約 79.1 倍 |
| 全月非零 OD 彙總前 20 名 | 3529.671 | 114.032 | 43.037 | DuckDB 約 82.0 倍 |

倍數使用 `MySQL 中位數 ÷ DuckDB 中位數`；小於 1 時表中改呈現 MySQL 較快的倍數。
兩種 DuckDB 設定均直接查詢同一份 Parquet，沒有預先計算查詢答案。

## 2. 資料完全相同嗎？

- 來源：專案 `data/interim/clean_od/202607.parquet`，即 2026 年 7 月整月捷運 OD。
- **兩邊各 8,366,652 列**，原始 0 人次也保留；不是只挑活動或北門的子集。
- 之所以選這個月，是 MySQL `original_data` 與清理後 Parquet 都有完整月份；沒有拿 MySQL 兩個月與 Parquet 全部年份比較。
- MySQL 獨立測試表從現有 `original_data` 擷取該月，將日期、時段、人次轉為 DATE／SMALLINT／BIGINT，站名去頭尾空白。
- 依 `source_row` 排序，逐列序列化來源列號、日期、時段、起站、迄站、人次，再計算 SHA-256；兩邊全部資料的雜湊一致。
- 每次查詢都核對完整輸出列數與 SHA-256，包含暖機；所有 108 次均一致。計時結束後才做結果雜湊。
- 每個查詢都有固定 `ORDER BY`，熱門 OD 同人次時依起迄站排序，避免不穩定排序影響核對。

全部輸入資料共同 SHA-256：

```text
7edd3b8791d0a5309cc366fa39a909ebf2939f8a1a96293ad39f3a1090cdf600
```

## 3. 如何公平計時

1. 先建立資料及索引、執行 `ANALYZE TABLE`、核對資料，全部排除在查詢時間之外。
2. MySQL 使用持續開啟的本機 `mysql` CLI 連線；DuckDB 使用持續開啟的 Python API 連線。沒有逐次啟動 MySQL 程序。
3. 量測 Python 呼叫端從送出查詢到**完整取得結果**的牆鐘時間。包含 SQL 執行、結果傳輸與取得結果的成本；不稱為純 CPU 或純引擎時間。
4. 每個查詢的各設定先各暖機 2 次，再各量 7 次；每輪用固定種子 20261020 打亂引擎順序，依序執行，沒有同時讓兩引擎競爭 CPU。
5. 沒有重啟 MySQL、清除作業系統快取或修改全域設定。這是重複查詢情境，**不是冷啟動／冷快取測試**；暖機不代表整個 InnoDB 索引都容得進 buffer pool。
6. 保留所有逐次耗時、執行計畫與結果指紋，不只保留最快一次。另嘗試擷取 MySQL Performance Schema；沒有取得時不混入其他口徑數字。

MySQL 的 `server_elapsed_ms` 是伺服器陳述式經過時間，可能含等待／輸出，不能稱為 CPU 時間：

| 查詢 | Python 呼叫端中位數（ms） | MySQL 陳述式中位數（ms） | ROWS_EXAMINED 中位數 |
|---|---:|---:|---:|
| q1_beimen_day | 0.727 | 0.483 | 648 |
| q2_four_stations | 103.220 | 102.377 | 281,976 |
| q3_network_hourly | 3092.604 | 3091.250 | 8,366,652 |
| q4_top_od | 3529.671 | 3529.223 | 8,366,672 |

## 4. 硬體、設定與索引

| 項目 | 本次設定 |
|---|---|
| CPU | AMD Ryzen 5 7600X 6-Core Processor |
| 實體核心／邏輯處理器 | 6／12 |
| 系統可見 RAM | 31.15 GiB |
| Python | 3.10.11 |
| DuckDB | 1.5.6 |
| MySQL | 8.0.44，InnoDB |
| MySQL buffer pool | 128 MiB，沿用本機設定 |
| DuckDB 保守設定 | 1 執行緒、`memory_limit='128MiB'` |
| DuckDB 專案設定 | 4 執行緒、`memory_limit='2GB'` |
| MySQL 字串排序 | `utf8mb4_bin`；本資料查詢輸出已與 DuckDB 核對 |

**MySQL buffer pool 與 DuckDB memory_limit 不是相同的記憶體定義。**
兩者都可能有額外記憶體與作業系統快取；此設定敏感度分析不宣稱 RAM 使用量或 CPU 資源被嚴格配平。
本次也沒有額外啟動或調整一台 2 GB buffer pool 的 MySQL，因此不能把測得倍數視為充分調校 MySQL 的效能上限。

MySQL 主鍵：`PRIMARY KEY (source_row)`。另建三個索引：

```sql
CREATE INDEX ix_station_date_hour_count
ON od (origin_station, service_date, source_hour, passengers);

CREATE INDEX ix_station_hour_count
ON od (origin_station, source_hour, passengers);

CREATE INDEX ix_od_count
ON od (origin_station, destination_station, passengers);
```

DuckDB 直接讀取 Parquet，未建立額外索引。兩邊資料內容相同，但儲存布局、壓縮、索引與讀取介面不同；
因此這是**本專案兩種實作方案的比較**，不是控制儲存格式後的單一引擎演算法實驗。

## 5. 索引設計的敏感度比較

先測只提供「車站、日期、時段、人次」索引，再補上支援兩個大型分組的索引。
下表保留 MySQL 改善前後的結果；最上面的正式比較採補齊索引的版本。
兩輪分開執行，未同步測量，差異也可能受快取與背景負載影響。

| 查詢 | 只有篩選索引（ms） | 補齊分組索引（ms） | 前者耗時／後者耗時 |
|---|---:|---:|---:|
| 北門單日 18–23 時逐時人次 | 0.734 | 0.727 | 1.01 |
| 四站全月晚間逐日逐時人次 | 108.746 | 103.220 | 1.05 |
| 全站全月依車站與時段彙總 | 22449.391 | 3092.604 | 7.26 |
| 全月非零 OD 彙總前 20 名 | 15846.117 | 3529.671 | 4.49 |

第一輪的原始紀錄保留於 [station_index/benchmark.json](station_index/benchmark.json)。
索引能改變 MySQL 的計畫與表現，因此不能以未調整索引時的最大差距代表所有 MySQL 使用情境。

## 6. 為何仍選 DuckDB 處理人流分析？

1. **符合研究的查詢形態。** 我們經常從整月 OD 做多站、時段、起迄站的分組加總；本次量測直接涵蓋這些工作。
2. **直接沿用已清理的 Parquet。** 不必為每次新的研究分組另設計資料庫索引；也不用先搬整批原始列進 pandas。
3. **欄位裁剪、條件下推、向量化與平行執行適合這類分析。** 官方機制可解釋優勢，但本實驗沒有逐一關閉機制，因此不能把某個倍數完全歸因於其中一項。
4. **保留 MySQL 的角色。** 活動、天氣、年度經費等關聯資料仍由 MySQL 管理；精準小範圍查詢也可能由 MySQL 勝出。

對老師可以這樣說：

> 我們使用同一個月約 837 萬筆捷運 OD，在本機比較了 DuckDB 直接讀 Parquet 與有索引的 MySQL。
> 查詢結果經核對一致。DuckDB 在多站與全月彙總較快，但 MySQL 在單站單日索引查詢更快。
> 因此選用 DuckDB 負責大量人流分析，並保留 MySQL 管理關聯資料；不是宣稱 DuckDB 在所有工作都比較快。

## 7. 準備成本與儲存空間

- MySQL 測試表轉型匯入：49.73 秒。
- 第一個涵蓋索引：16.29 秒；額外兩個分組索引：28.69 秒。
- 現有 Parquet 檔案：16.08 MiB。
- MySQL `DATA_LENGTH`：508.00 MiB；`INDEX_LENGTH`：1019.95 MiB；`DATA_FREE`：5.00 MiB。這是 InnoDB 報告的配置空間，非邏輯資料大小。
- **Parquet 是已存在的清理成果，本次未計時其原始 CSV 清理與轉檔。** 所以不能把上述 MySQL 準備成本當作完整 ETL 的公平速度比較。
- 測試結束已刪除本次專用資料庫；沒有修改或刪除原始資料表，也沒有更改 MySQL 全域設定。

## 8. 查詢定義與重現

兩邊使用以下相同 SQL；DuckDB 的 `od` 是讀取 Parquet 的 view，MySQL 的 `od` 是獨立的實體測試表。
Q1 的 2026-07-29 是固定查詢日期，不用這次效能測試判定它是否為實際活動日。

### q1_beimen_day：北門單日 18–23 時逐時人次

```sql
SELECT source_hour,SUM(passengers) AS total
                  FROM od WHERE origin_station='北門'
                  AND service_date='2026-07-29' AND source_hour BETWEEN 18 AND 23
                  GROUP BY source_hour ORDER BY source_hour;
```

### q2_four_stations：四站全月晚間逐日逐時人次

```sql
SELECT service_date,origin_station,source_hour,SUM(passengers) AS total
                  FROM od WHERE origin_station IN ('北門','大橋頭站','雙連','民權西路')
                  AND source_hour BETWEEN 18 AND 23
                  GROUP BY service_date,origin_station,source_hour
                  ORDER BY service_date,origin_station,source_hour;
```

### q3_network_hourly：全站全月依車站與時段彙總

```sql
SELECT origin_station,source_hour,SUM(passengers) AS total
                  FROM od GROUP BY origin_station,source_hour
                  ORDER BY origin_station,source_hour;
```

### q4_top_od：全月非零 OD 彙總前 20 名

```sql
SELECT origin_station,destination_station,SUM(passengers) AS total
                  FROM od WHERE passengers>0
                  GROUP BY origin_station,destination_station
                  ORDER BY total DESC,origin_station,destination_station LIMIT 20;
```

在專案根目錄執行，需已具備本機資料、已設定的 `codex-local` MySQL login path，以及建立／移除獨立測試資料庫的權限：

```powershell
# 重跑最終比較；不變更正式資料表
.\.venv\Scripts\python.exe -X utf8 scripts/benchmark_duckdb_mysql.py --index-profile workload

# 由量測檔重建本 README
.\.venv\Scripts\python.exe -X utf8 scripts/report_duckdb_benchmark.py
```

只重跑原本篩選索引的敏感度版本，可使用 `--index-profile station`；其輸出寫入 `station_index/`。
執行測試會先做完整資料核對；不一致即停止，不發表速度比較。
若外部強制終止程序而留下 `bench_duckdb_...` 資料庫，先依 `.local/duckdb_benchmark/<run_id>/checkpoint.json` 核對名稱，再處理該次測試資料庫。

| 檔案 | 內容 |
|---|---|
| [summary.csv](summary.csv) | 各查詢中位數、最小／最大值、倍數 |
| [timings.csv](timings.csv) | 暖機與正式量測全部紀錄 |
| [benchmark.json](benchmark.json) | 設定、原始 SQL、版本、資料與程式 SHA-256 |
| [query_plans.json](query_plans.json) | MySQL 與 DuckDB 的 EXPLAIN |
| [量測程式](../../scripts/benchmark_duckdb_mysql.py) | 建表、核對、計時與清理流程 |
| [報告程式](../../scripts/report_duckdb_benchmark.py) | 從實測產生中文說明 |

測量範圍供檢查，不當作統計信賴區間：

| 查詢 | 設定 | 7 次最小–最大值（ms） | 輸出列數 |
|---|---|---:|---:|
| q1_beimen_day | MySQL | 0.536–0.888 | 6 |
| q1_beimen_day | DuckDB／1 執行緒 | 3.532–4.196 | 6 |
| q1_beimen_day | DuckDB／4 執行緒 | 2.629–3.086 | 6 |
| q2_four_stations | MySQL | 102.601–105.637 | 744 |
| q2_four_stations | DuckDB／1 執行緒 | 51.405–52.714 | 744 |
| q2_four_stations | DuckDB／4 執行緒 | 16.692–18.391 | 744 |
| q3_network_hourly | MySQL | 3089.854–3098.764 | 2,499 |
| q3_network_hourly | DuckDB／1 執行緒 | 103.280–105.661 | 2,499 |
| q3_network_hourly | DuckDB／4 執行緒 | 38.446–41.206 | 2,499 |
| q4_top_od | MySQL | 3519.642–3724.368 | 20 |
| q4_top_od | DuckDB／1 執行緒 | 113.093–116.181 | 20 |
| q4_top_od | DuckDB／4 執行緒 | 42.125–53.963 | 20 |

## 9. 參考資料與限制

- [DuckDB：設計與向量化執行](https://duckdb.org/why_duckdb)。
- [DuckDB：Parquet 欄位裁剪與條件下推](https://duckdb.org/docs/current/data/parquet/overview#partial-reading)。
- [DuckDB：平行處理與大於記憶體的工作](https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads)。
- [MySQL：GROUP BY 與索引最佳化](https://dev.mysql.com/doc/refman/8.0/en/group-by-optimization.html)。
- [MySQL：InnoDB buffer pool](https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html)。

本次是單機、單月份、四種查詢、順序執行的重複量測。沒有測全部 1.41 億列、遠端傳輸、
多人並行更新、不同硬體、不同資料排序或充分調校的 MySQL 記憶體；也沒有做統計顯著性宣稱。
實際部署若改變資料或設定，需重新量測。官方文件支持技術機制，表中的速度數字只來自本次實驗。
