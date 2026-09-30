# 大稻埕煙火事前 7 天聲量：自動抓取

SQL 檔與資料備份不提交 Git。隊員需先透過私密管道取得所需 SQL、建立資料庫並設定自己的 MySQL login path，再執行下列程式；不要共用或上傳帳密檔。

日期直接讀自 MySQL 的 `dadaocheng_fireworks_events`。預設納入所有 `held`、`cancelled`、`postponed` 日期；目前共 26 個日期（21 場施放、4 個取消日、1 個延期日）。每個原訂／實際日期 D 只查 D−7～D−1，不含 D。

## 執行

在專案根目錄執行下列命令，即會自動從 Google Trends 網頁使用的資料端點下載、驗證、保存原始 JSON，計算平均並寫入 MySQL：

```powershell
python mysql/trends_features.py fetch --commit
```

目前預設關鍵字為「大稻埕煙火」、臺灣 `TW`、Google 網頁搜尋。使用 Python 標準函式庫，不需安裝 pytrends 或其他爬蟲套件。這是網頁端點自動化，**不是 Google 官方支援的 Trends API**，網站若改版可能需要修改程式。

常用選項：

```powershell
# 查看從資料庫產生的日期、活動狀態與查詢網址
python mysql/trends_features.py plan

# 只抓 2017 年並寫入
python mysql/trends_features.py fetch --year 2017 --commit

# 只抓取消與延期日期
python mysql/trends_features.py fetch --status cancelled --status postponed --commit

# 自動下載並預覽；不寫入 MySQL
python mysql/trends_features.py fetch

# 完全不連 Google，僅重用本機快取寫入
python mysql/trends_features.py ingest --commit
```

`--event-date 2017-08-26` 可指定一個資料庫中已存在的日期。`--keyword`、`--geo` 可調整查詢詞和地區。每次 Google 請求至少間隔 15 秒（可用 `--delay` 指定 10–60 秒），沒有平行大量請求。

## 抓取限制與續跑

若 Google 回傳 HTTP 429、401、403 或要求重新導向／驗證，程式會停止整批請求，保留已完成資料。一般來源格式或缺值問題會記錄該場失敗，再處理下一場。失敗與缺資料不會轉成聲量 0。稍後重跑同一命令會先重用已驗證的快取；不會重新下載已完成場次，也不會把相同來源重複新增成相同場次特徵。

快取存於 `data/trends/web/`，檔名包含活動日期與查詢設定雜湊，因此不同關鍵字或地區不會互相覆蓋。`last_run.json` 記錄最近一次執行的成功、失敗、受限及尚未處理數。Google 的圖表 token 和 cookie 不保存到檔案。

## 三個平均怎麼算

以 2017/8/26 為例，唯一查詢區間是 **2017/8/19～8/25**：

| 欄位 | 使用資料 |
|---|---|
| `interest_1d` | 8/25 的每日指標 |
| `interest_3d_avg` | 8/23～8/25 的每日指標平均 |
| `interest_7d_avg` | 8/19～8/25 的每日指標平均 |

同一場的三個平均全部來自同一次七日查詢，沒有把 1、3、7 天分開重新查詢。

若來源回傳 `DAY`，程式要求恰好 7 個完整日值，依時間戳記記錄 `UTC` 或 `Asia/Taipei` 日界線。若回傳 `HOUR`，程式要求臺灣時間 D−7 00:00 至 D 00:00 前的 **168 個小時**全部齊全，先將每天 24 個指標取平均，再算三個特徵。小時指標的日平均是本研究衍生指標，不能稱為 Google 原生的日搜尋熱度；`source_resolution` 和 `samples_in_day` 會保留這項差異。來源若少一小時、少一天、有重複或未完成時段，均不寫入有效特徵。

## 資料庫與來源追溯

- `google_trends_imports`：每份來源快照一列，記錄查詢窗、字詞、地區、來源網址、SHA-256、日界線及來源粒度。
- `google_trends_daily`：主鍵 `(import_id, search_date)`；保存來源日指標或每天 24 個小時指標的平均。
- `event_trends_features`：主鍵 `(event_date, query_text, geo_code)`；保留三個平均以及 `import_id`。活動是否取消、延期及其原因，透過 `event_date` 接回事件表。

本工作區已套用資料庫遷移。新資料庫使用目前的 `003_google_trends.sql`；只有舊版仍含 `csv_sha256` 欄位的資料庫，才需要執行一次 `004_google_trends_web.sql`。

```sql
SELECT e.event_date, e.event_status, e.not_held_reason,
       f.interest_1d, f.interest_3d_avg, f.interest_7d_avg,
       i.time_basis, i.source_resolution
FROM taipei_metro_analysis.dadaocheng_fireworks_events AS e
LEFT JOIN taipei_metro_analysis.event_trends_features AS f
  ON f.event_date = e.event_date
 AND f.query_text = '大稻埕煙火' AND f.geo_code = 'TW'
LEFT JOIN taipei_metro_analysis.google_trends_imports AS i
  ON i.import_id = f.import_id
ORDER BY e.event_date;
```

## 資安

MySQL 密碼只從本機 login path 取得，不放進原始碼、命令列或 Google 請求。資料庫 SQL 由標準輸入傳送，文字使用十六進位字面值，日期與數值先驗證；不啟用 `LOCAL INFILE`。正式部署應為 `--login-path` 配置只具必要資料表權限的專用帳號。

網路只請求固定的 `https://trends.google.com` 路徑，使用系統 TLS 憑證驗證並拒絕重新導向。不讀取瀏覽器登入 cookie、不使用環境代理或 `.netrc` 帳密，也不輪替代理或繞過驗證碼。送給 Google 的內容只有公開關鍵字、地區與查詢日期，不包含捷運刷卡資料或 MySQL 帳密。回應有逾時與大小限制。

## 做回歸前

七日資料已足夠計算這三個平均。但 Google Trends 每次查詢都各自正規化為 0–100，因此這些值描述每場活動前一週內的相對搜尋興趣，**尚未校準成能直接比較各場活動聲量高低的共同尺度**。縮短為七日不會解決這個問題；跨場回歸需另做共同尺度校準，或明確把指標解釋成一週內的相對變化。0 也可能代表搜尋量低，不等於無人搜尋。

若 `time_basis = 'UTC'`，D−1 日值涵蓋至臺灣時間 D 日 08:00。這只是統計時段終點，不保證 Google 已於當時發布完整資料；真實預測還要考慮資料可取得時間。`DAY` 原生日指標與 `HOUR` 的日平均須分別檢查，不能默認完全等價。取消結果若在預測時點尚未公告，也不能直接當成事前已知參數。

官方說明：[Google Trends 資料、正規化與時區](https://support.google.com/trends/answer/4365533?hl=zh-Hant)、[官網歷史範圍可回溯到 2004 年](https://newsinitiative.withgoogle.com/en-gb/resources/trainings/google-trends/advanced-google-trends/)。實際能否取得 2017 年該關鍵字的完整七日資料，須以成功的網頁回應為準。
