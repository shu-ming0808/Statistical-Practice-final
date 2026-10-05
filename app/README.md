# 大稻埕人流網站

React＋shadcn 黑灰介面、MapLibre 地圖、FastAPI 與現有 MySQL。提供「人流展示」及「資料分析」兩頁；第一版讀取活動日期、各站逐時人流與資料完整性，分析模型尚未加入。

## 啟動

主機需有 Node.js 22.12 以上的相容 LTS、uv、本機 MySQL，以及既有的 `codex-local` MySQL login path。在專案根目錄執行：

```powershell
# 首次安裝；--inexact 保留原有的 notebook、社群分析等套件
uv sync --inexact --extra app
npm.cmd --prefix app/frontend ci
npm.cmd --prefix app/frontend run build

# 背景啟動／停止網站
.\app\start.ps1
.\app\start.ps1 -Stop
```

啟動後開啟 [http://127.0.0.1:8100](http://127.0.0.1:8100)。後續改完前端可在停止網站後執行 `.\app\start.ps1 -Build`，重新安裝鎖定套件、建置並啟動。一般啟動不下載套件；記錄放在 `app/.runtime/`。如果 PowerShell 阻擋本機腳本，可用 `powershell.exe -ExecutionPolicy Bypass -File .\app\start.ps1`，只作用於這次執行。

資料連線使用本機 MySQL 工具與加密登入設定，網站不接收或保存資料庫密碼，僅執行固定的唯讀查詢。需要其他登入名稱或工具路徑時，在啟動前設定 `APP_MYSQL_LOGIN_PATH`、`APP_MYSQL_BIN`。資料庫異常時會顯示錯誤，不以假資料代替。

## 隊員透過 Tailscale 使用

三人各自使用帳號加入並獲准存取同一個 tailnet；使用網站的人不需安裝 Python 或 MySQL。主機必須持續開機，MySQL、網站及 Tailscale 都要運作。

目前主機的私人網址：[開啟稻埕觀測室](https://desktop-4rv84m1.tail7191d4.ts.net:8443/)。已設定 8443 轉送到本機 8100，無須每次啟動重新設定。

先檢查既有轉送，保留其他服務：

```powershell
tailscale serve status
tailscale funnel status
# 確認 8443 未被使用後，新增私人入口
tailscale serve --bg --https=8443 http://127.0.0.1:8100
```

使用 Tailscale 回傳的 `https://主機名稱.tailnet名稱.ts.net:8443` 網址連線。若 tailnet 存取規則有限制，需允許這三位成員連線到主機的 TCP 8443。**此專案不使用 Funnel，也不修改既有 443／8000 的轉送。** 私人入口需由主機管理者完成首次 HTTPS 授權；`start.ps1` 不會自動更動 Tailscale。

## 資料與示意範圍

- 地圖上的會場到車站路線、粒子、人數分配與分鐘變化是示意設定，不是民眾真實軌跡；固定站點／入口位置不能證明實際行走路徑。
- MySQL 顯示的是活動日原始 OD 時段的站點彙總。目的站人數是「同一來源時段」加總，不能直接當成真實出站時間；缺值不補為零。
- 不同隊員各自操作日期、參數與播放進度，不會修改 MySQL 或影響其他人。回歸、M/M/c、候車時間與正式疏運模型尚未實作。

```text
app/
├── frontend/       # React 畫面、地圖與建置後的 dist/
├── backend/        # FastAPI、MySQL 唯讀查詢與 API 測試
├── start.ps1       # Windows 啟動／停止
└── README.md
```

既有 `web/meta-review/` 是公開隱私與刪除說明網站，與本網站分開保留。
