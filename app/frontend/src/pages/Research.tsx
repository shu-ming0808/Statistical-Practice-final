import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DispatchComparison } from "@/components/DispatchComparison";
import { DecisionReplay } from "@/components/DecisionReplay";
import {
  Activity,
  TrainFront,
  ArrowUpRight,
  Clock3,
  Download,
  RefreshCw,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { numberText } from "@/lib/api";
import type { DispatchData } from "@/lib/dispatch";
import {
  departureTime,
  publicFile,
  scenarioRows,
  SHAPES,
  waitValue,
  type Research,
} from "@/lib/research";
import "./Research.css";

const GITHUB =
  "https://github.com/shu-ming0808/Statistical-Practice-final/blob/main/";
const TOOLTIP = {
  background: "#202023",
  border: "1px solid #48484d",
  borderRadius: 10,
  color: "#eee",
};
const LINES: Record<string, string> = {
  G: "松山新店線",
  R: "淡水信義線",
  O: "中和新蘆線",
};
const format = (v: unknown) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
type Tab = "model" | "queue" | "replay" | "operations" | "dispatch";

function Stat({
  label,
  value,
  unit,
  note,
}: {
  label: string;
  value: string;
  unit: string;
  note: string;
}) {
  return (
    <div className="research-stat">
      <span>{label}</span>
      <div>
        <strong className="num">{value}</strong>
        <small>{unit}</small>
      </div>
      <p>{note}</p>
    </div>
  );
}
function ChartNote({ children }: { children: React.ReactNode }) {
  return <p className="research-note">{children}</p>;
}

export function ResearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<Research>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const requestedTab = searchParams.get("tab");
  const tab: Tab = requestedTab === "dispatch" || requestedTab === "queue" || requestedTab === "replay" || requestedTab === "operations" ? requestedTab : "model";
  const [event, setEvent] = useState("2024-08-10");
  useEffect(() => {
    const controller = new AbortController();
    fetch(publicFile("research.json"), { signal: controller.signal })
      .then((r) => {
        if (!r.ok)
          throw new Error("成果快照尚未發布，請重新整理或查看 GitHub 報告。");
        return r.json();
      })
      .then(setData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [reload]);
  if (error)
    return (
      <div className="empty-state" role="alert">
        <h1>成果暫時無法載入</h1>
        <p>{error}</p>
        <Button
          variant="outline"
          onClick={() => {
            setError("");
            setReload((n) => n + 1);
          }}
        >
          <RefreshCw size={15} />
          重試
        </Button>
      </div>
    );
  if (!data)
    return (
      <div className="empty-state" aria-busy="true">
        正在載入模型與候車成果…
      </div>
    );
  const dates = [...new Set(data.model.hourly.map((r) => r.event_date))];
  return (
    <div className="research-page">
      <div className="research-intro">
        <div>
          <h1>詳細預測資訊</h1>
          <p>比較預測、候車與 18:00–23:00 增減班方案，查看四站相關旅次的轉乘影響。</p>
        </div>
        <Badge variant="outline">公開成果快照</Badge>
      </div>
      {tab !== "dispatch" && <div className="research-meta">
        更新：
        {new Date(data.generated_at).toLocaleString("zh-TW", {
          timeZone: "Asia/Taipei",
          hour12: false,
        })}
        （臺北）<span>模型：{data.model.name}</span>
        <span>候車範圍：北門雙向</span>
      </div>}
      <div className="research-toolbar">
        <div className="research-tabs" role="tablist" aria-label="研究成果分類">
          {(
            [
              { id: "model", label: "預測結果", icon: Activity },
              { id: "queue", label: "候車情境", icon: Clock3 },
              { id: "replay", label: "單場案例", icon: Activity },
              { id: "operations", label: "班表與調度", icon: TrainFront },
              { id: "dispatch", label: "增減班比較", icon: Activity },
            ] as const
          ).map((t) => (
            <Button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              variant={tab === t.id ? "secondary" : "ghost"}
              onClick={() => setSearchParams({tab: t.id})}
            >
              <t.icon size={16} />
              {t.label}
            </Button>
          ))}
        </div>
        {tab !== "operations" && tab !== "dispatch" && (
          <label className="research-filter">
            驗證場次
            <select value={event} onChange={(e) => setEvent(e.target.value)}>
              {dates.map((d) => (
                <option key={d}>{d}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {tab === "model" && <Model data={data} event={event} />}
      {tab === "queue" && <Queue data={data} event={event} />}
      {tab === "replay" && <DecisionReplay data={data} event={event} />}
      {tab === "operations" && <Operations data={data} />}
      {tab === "dispatch" && <DispatchComparison />}
      <div className="research-links">
        <a
          href={GITHUB + "result/forecast/report.md"}
          target="_blank"
          rel="noreferrer"
        >
          模型比較完整報告
          <ArrowUpRight size={14} />
        </a>
        <a
          href={GITHUB + "result/queue_dispatch_v1/report.md"}
          target="_blank"
          rel="noreferrer"
        >
          候車假設與結果
          <ArrowUpRight size={14} />
        </a>
        <a
          href={GITHUB + "docs/metro_operations.md"}
          target="_blank"
          rel="noreferrer"
        >
          官方資料、車上人數估法
          <ArrowUpRight size={14} />
        </a>
      </div>
    </div>
  );
}

function Model({ data, event }: { data: Research; event: string }) {
  const rows = data.model.hourly
    .filter((r) => r.event_date === event)
    .map((r) => ({ ...r, time: `${r.hour}:00` }));
  const current = data.model.events.find((r) => r.event_date === event)!;
  const m = data.model.metrics;
  return (
    <>
      <div className="research-stats">
        <Stat
          label="全部九場 · MAE"
          value={format(m.mae)}
          unit="人次"
          note="54 個北門小時；平均絕對誤差"
        />
        <Stat
          label="全部九場 · RMSE"
          value={format(m.rmse)}
          unit="人次"
          note="較重視大幅預測誤差"
        />
        <Stat
          label={`${event} · 尖峰預測差`}
          value={numberText(+current.peak_error)}
          unit="人次"
          note={`${current.actual_peak_hour}:00 同一時段比較；${format(current.peak_error_percent)}%`}
        />
      </div>
      <section className="panel research-panel">
        <div className="research-panel-head">
          <div>
            <span className="eyebrow">BEIMEN / HOURLY FORECAST</span>
            <h2>北門逐時：實際與預測</h2>
          </div>
          <span>{event} · 18–24 時</span>
        </div>
        <div
          className="research-chart"
          role="img"
          aria-label={`${event} 北門逐時實際與預測長條圖`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} barGap={5}>
              <CartesianGrid vertical={false} stroke="#303034" />
              <XAxis dataKey="time" />
              <YAxis width={62} tickFormatter={(v) => numberText(v)} />
              <Tooltip contentStyle={TOOLTIP} formatter={format} />
              <Legend />
              <Bar
                isAnimationActive={false}
                dataKey="actual"
                name="實際"
                fill="#d8d8d7"
                radius={[4, 4, 0, 0]}
              />
              <Bar
                isAnimationActive={false}
                dataKey="predicted"
                name="模型預測"
                fill="#8194aa"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <ChartNote>
          單位：人次／原始 OD
          小時格。這是所有進站旅次，不能直接當成活動參加人數。
          {data.model.selection_note}
        </ChartNote>
        <details>
          <summary>查看本場精確數值</summary>
          <p className="mobile-table-hint">左右滑動查看完整表格</p>
          <div
            className="research-table-wrap"
            tabIndex={0}
            role="region"
            aria-label="可左右捲動的資料表"
          >
            <table>
              <thead>
                <tr>
                  <th>時段</th>
                  <th>實際</th>
                  <th>預測</th>
                  <th>差（預測−實際）</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.hour}>
                    <td>{r.time}</td>
                    <td>{numberText(r.actual)}</td>
                    <td>{numberText(r.predicted)}</td>
                    <td>{numberText(r.predicted - r.actual)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
      <div className="research-explanation">
        <div>
          <h3>目前保留的模型</h3>
          <p>
            四站訓練的 XGBoost，北門散場樣本訓練權重 4
            倍。沿用封存預測，本頁沒有重新訓練或挑選每場最好的模型。
          </p>
        </div>
        <div>
          <h3>接到候車模擬</h3>
          <p>
            取預測的逐時人數，拆成分鐘與兩個方向，再配合班表及剩餘空間。實際人流只用於檢查誤差，沒有放入候車情境輸入。
          </p>
        </div>
      </div>
      <ChartNote>
        活動前一天預測的歷史資料可得性與 OD
        時段仍有研究限制，詳見模型報告。這九場已反覆檢視，仍需新活動驗證。
      </ChartNote>
    </>
  );
}

function Queue({ data, event }: { data: Research; event: string }) {
  const [fraction, setFraction] = useState(0.5);
  const [share, setShare] = useState(0.5);
  const [center, setCenter] = useState(30);
  const [direction, setDirection] = useState("往新店／台電大樓");
  const selected = scenarioRows(
    data,
    event,
    center,
    share,
    fraction,
    direction,
  );
  const chart = SHAPES.map((s, i) => ({
    name: s.name,
    wait: waitValue(selected[i]),
    queue: selected[i] ? +selected[i]!.max_queue : null,
  }));
  const sensitivity = [0.25, 0.5, 0.75].map((f) => {
    const r = scenarioRows(data, event, center, share, f, direction);
    return {
      space: `${numberText(2220 * f)} 人`,
      均勻: waitValue(r[0]),
      寬峰: waitValue(r[1]),
      集中峰: waitValue(r[2]),
    };
  });
  const concentrated = selected[2];
  return (
    <>
      <div className="research-callout">
        <TrainFront size={22} />
        <div>
          <strong>北門 G13｜固定剩餘空間的敏感度情境</strong>
          <p>
            官方整列容量上限參考值 2,220 人（370 ×
            6）。下方採「下車後仍可再載的空間」，不是把全列容量都留給北門。
          </p>
          <p>本頁的剩餘空間 50% 與雙向各 50% 是兩個不同假設。新版全線模型用 OD 分配方向並累計沿途上下車，請見<a href="#/research?tab=dispatch">增減班比較與逐班載量</a>。</p>
        </div>
      </div>
      <div className="research-controls panel">
        <label className="research-filter">
          行車方向
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
          >
            <option value="往松山">往松山</option>
            <option value="往新店／台電大樓">往新店（本版晚間班表）</option>
          </select>
        </label>
        <label className="research-filter">
          每班剩餘空間
          <select
            value={fraction}
            onChange={(e) => setFraction(+e.target.value)}
          >
            {[0.25, 0.5, 0.75].map((f) => (
              <option value={f} key={f}>
                {numberText(2220 * f)} 人 · 上限 {f * 100}%
              </option>
            ))}
          </select>
        </label>
        <label className="research-filter">
          往松山：往新店
          <select value={share} onChange={(e) => setShare(+e.target.value)}>
            {[0.4, 0.5, 0.6].map((s) => (
              <option key={s} value={s}>
                {s * 100}：{Math.round((1 - s) * 100)}
              </option>
            ))}
          </select>
        </label>
        <label className="research-filter">
          尖峰小時內集中位置
          <select value={center} onChange={(e) => setCenter(+e.target.value)}>
            {[15, 30, 45].map((c) => (
              <option key={c} value={c}>
                第 {c} 分鐘
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="research-stats">
        <Stat
          label="集中峰・平均候車"
          value={format(waitValue(concentrated))}
          unit="分鐘"
          note="所選場次、方向與假設"
        />
        <Stat
          label="集中峰・最大候車"
          value={concentrated ? numberText(+concentrated.max_queue) : "—"}
          unit="人"
          note="瞬間排隊量；不是月台實測值"
        />
        <Stat
          label="所選方向預測需求"
          value={selected[0] ? numberText(+selected[0].forecast_demand) : "—"}
          unit="人次"
          note="三種形狀維持相同逐時總量"
        />
      </div>
      <div className="research-two-charts">
        <section className="panel research-panel">
          <h2>同樣人數，集中程度不同</h2>
          <p>平均候車時間（分鐘）</p>
          <div
            className="research-chart small"
            role="img"
            aria-label="三種到達形狀平均候車比較"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart}>
                <CartesianGrid vertical={false} stroke="#303034" />
                <XAxis dataKey="name" />
                <YAxis width={42} />
                <Tooltip contentStyle={TOOLTIP} formatter={format} />
                <Bar
                  isAnimationActive={false}
                  name="平均候車（分）"
                  dataKey="wait"
                  fill="#8194aa"
                  radius={[5, 5, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="panel research-panel">
          <h2>每班能再載多少人？</h2>
          <p>所選場次與方向的容量敏感度</p>
          <div
            className="research-chart small"
            role="img"
            aria-label="每班剩餘空間與候車時間"
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={sensitivity}>
                <CartesianGrid vertical={false} stroke="#303034" />
                <XAxis dataKey="space" />
                <YAxis width={42} />
                <Tooltip contentStyle={TOOLTIP} formatter={format} />
                <Legend />
                {["均勻", "寬峰", "集中峰"].map((k, i) => (
                  <Line
                    isAnimationActive={false}
                    key={k}
                    name={k}
                    dataKey={k}
                    stroke={["#d3d3d3", "#8194aa", "#a59b8d"][i]}
                    strokeWidth={2}
                    dot={{ r: 4 }}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
      <section className="panel research-panel">
        <div className="research-panel-head">
          <h2>情境結果明細</h2>
          <a href={publicFile("queue-scenarios.csv")} download>
            <Download size={15} />
            下載全部 1,134 組
          </a>
        </div>
        <p className="mobile-table-hint">左右滑動查看完整表格</p>
        <div
          className="research-table-wrap"
          tabIndex={0}
          role="region"
          aria-label="可左右捲動的資料表"
        >
          <table>
            <thead>
              <tr>
                <th>到達形狀</th>
                <th>平均候車（分）</th>
                <th>最大排隊人數</th>
                <th>終班後剩餘</th>
              </tr>
            </thead>
            <tbody>
              {selected.map((r, i) => (
                <tr key={SHAPES[i].id}>
                  <td>{SHAPES[i].name}</td>
                  <td>{format(waitValue(r))}</td>
                  <td>{r ? numberText(+r.max_queue) : "—"}</td>
                  <td>{r ? numberText(+r.residual_queue) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ChartNote>
          終班後未清空時，不回報完整平均候車。這裡的人數是流體近似，表格取整數顯示。
        </ChartNote>
      </section>
      <div className="research-explanation">
        <div>
          <h3>形狀是情境假設</h3>
          <p>
            只改變「模型預測最高的小時」：均勻、寬峰 σ=20 分、集中峰 σ=5
            分；其他小時均勻。加上站內步行 5
            分鐘。方向比例與剩餘空間都尚未實測校準。
          </p>
        </div>
        <div>
          <h3>目前班表的壓力比較</h3>
          <p>
            將 2024–2026 年九場預測套用 2026/8/30
            生效的一般北門班表，並以離站時刻近似完成上車。不是歷史活動當天實際候車，也尚未最佳化加班車。
          </p>
        </div>
      </div>
      <details className="panel research-panel">
        <summary>查看分鐘曲線示例與其假設</summary>
        <p>
          固定示例：2024-08-10、往新店、每班剩餘 1,110 人、方向各 50%、峰中心第
          30 分鐘。此圖不隨上方選項改變。
        </p>
        <img
          className="research-figure"
          src={publicFile("arrival_queue_comparison.png")}
          alt="固定示例的均勻、寬峰、集中峰抵達與候車曲線"
          loading="lazy"
        />
      </details>
      <ChartNote>
        只追蹤 18–23 時預測群體，初始候車
        0；未包含額外轉乘、午夜後新增需求、車門吞吐與月台管制。所有候車結果均為條件式估計。
      </ChartNote>
    </>
  );
}

function Operations({ data }: { data: Research }) {
  const [station, setStation] = useState("北門");
  const [line, setLine] = useState("G");
  const [day, setDay] = useState("activity");
  const [dispatch, setDispatch] = useState<DispatchData | null>(null);
  const [dispatchError, setDispatchError] = useState(false);
  const [event, setEvent] = useState("2024-08-10");
  const [weight, setWeight] = useState(1);
  const [source, setSource] = useState("");
  const [start, setStart] = useState(18);
  const [end, setEnd] = useState(25);
  const op = data.operations;
  useEffect(() => {
    const controller = new AbortController();
    fetch(publicFile("dispatch.json"), { signal: controller.signal })
      .then(r => { if (!r.ok) throw new Error("dispatch unavailable"); return r.json(); })
      .then((value: DispatchData) => {
        if (value.schema_version !== 1 || !value.no_wait_threshold) throw new Error("invalid dispatch snapshot");
        setDispatch(value);
      })
      .catch(() => { if (!controller.signal.aborted) setDispatchError(true); });
    return () => controller.abort();
  }, []);
  const eventDates = [...new Set(dispatch?.comparison.map(r => r.event_date) ?? [])];
  const eventWeekday = new Date(`${event}T12:00:00Z`).getUTCDay();
  const baselineDay = day === "activity"
    ? eventWeekday === 6 ? "saturday" : eventWeekday === 0 ? "sunday_holiday" : "weekday"
    : day;
  const stations = [...new Set(op.station_line.map((r) => r.station_label))];
  const lines = op.station_line
    .filter((r) => r.station_label === station)
    .map((r) => r.line_code);
  const tables = op.timetable.filter(
    (r) => r.station_label === station && r.line_code === line,
  );
  const table = tables.find((r) => r.source_id === source) || tables[0];
  const meta = op.source_snapshot.find((r) => r.source_id === table?.source_id);
  const departures = op.departure.filter(
    (r) =>
      r.source_id === table?.source_id &&
      r.day_type === baselineDay &&
      +r.departure_minute >= start * 60 &&
      +r.departure_minute < end * 60,
  );
  const eventComparison = dispatch?.comparison.find(r => r.event_date === event && r.waiting_weight === weight);
  const greenEvent = day === "activity" && station === "北門" && line === "G";
  const direction = table?.direction_text.includes("松山") ? "往松山" : "往新店";
  const eventDepartures = greenEvent ? (dispatch?.timetable ?? [])
    .filter(r => r.event_date === event && r.waiting_weight === weight && r.direction === direction && r.new_beimen)
    .filter(r => { const hour = Number(r.new_beimen.slice(0, 2)); return hour >= start && hour < end; })
    .sort((a, b) => a.new_beimen.localeCompare(b.new_beimen)) : [];
  const directionChanges = greenEvent ? (dispatch?.timetable ?? [])
    .filter(r => r.event_date === event && r.waiting_weight === weight && r.direction === direction && r.action !== "保留") : [];
  const paths = op.dispatch_path
    .filter((r) => r.station_label === station && r.line_code === line)
    .filter((r, i, all) => all.findIndex((a) => a.origin === r.origin) === i);
  function changeStation(s: string) {
    setStation(s);
    setLine(op.station_line.find((r) => r.station_label === s)!.line_code);
    setSource("");
  }
  return (
    <>
      <div className="research-controls panel">
        <label className="research-filter">
          研究車站
          <select
            value={station}
            onChange={(e) => changeStation(e.target.value)}
          >
            {stations.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="research-filter">
          路線
          <select
            value={line}
            onChange={(e) => {
              setLine(e.target.value);
              setSource("");
            }}
          >
            {lines.map((l) => (
              <option value={l} key={l}>
                {LINES[l]}
              </option>
            ))}
          </select>
        </label>
        <label className="research-filter">
          方向
          <select
            value={table?.source_id || ""}
            onChange={(e) => setSource(e.target.value)}
          >
            {tables.map((t) => (
              <option key={t.source_id} value={t.source_id}>
                {t.direction_text}
              </option>
            ))}
          </select>
        </label>
        <label className="research-filter">
          日型
          <select value={day} onChange={(e) => setDay(e.target.value)}>
            <option value="activity">活動情境（一般表＋模擬調度）</option>
            <option value="weekday">平日</option>
            <option value="saturday">週六</option>
            <option value="sunday_holiday">週日／國定假日</option>
          </select>
        </label>
        {day === "activity" && <label className="research-filter">活動日期
          <select value={event} onChange={e => setEvent(e.target.value)}>
            {eventDates.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>}
        {day === "activity" && <label className="research-filter">候車成本權重 λ
          <select value={weight} onChange={e => setWeight(Number(e.target.value))}>
            {(dispatch?.waiting_weights ?? [1]).map(w => <option key={w} value={w}>{w}</option>)}
          </select>
        </label>}
      </div>
      <section className="panel research-panel">
        <div className="research-panel-head">
          <div>
            <span className="eyebrow">{greenEvent ? "DISPATCH SCENARIO" : "OFFICIAL DEPARTURES"}</span>
            <h2>{station} · {greenEvent ? "活動模擬班表" : "一般離站時刻表"}</h2>
          </div>
          {meta && (
            <a href={meta.source_url} target="_blank" rel="noreferrer">
              一般班表來源 PDF
              <ArrowUpRight size={15} />
            </a>
          )}
        </div>
        <p>
          生效：{meta?.effective_from} · 擷取：{meta?.retrieved_at_utc}{" "}
          UTC。{greenEvent ? "以下是以 2026-08-30 一般班表為基準的研究模擬，並非活動當日實際營運紀錄。" : "此處只顯示一般班表；調度模型只調整松山新店線，其他路線維持原表。"}
        </p>
        {day === "activity" && <p>活動 {event} · 參照日型：{baselineDay === "weekday" ? "平日" : baselineDay === "saturday" ? "週六" : "週日"}（按星期選取；國定假日須另核對）。{greenEvent && eventComparison && `兩方向合計增 ${eventComparison.added}、減 ${eventComparison.removed}、移時 ${eventComparison.retimed} 班。`}</p>}
        {greenEvent && dispatch && <p>目前方向 {direction}：新增 {directionChanges.filter(r => r.action === "增班").length} 班、取消 {directionChanges.filter(r => r.action === "取消").length} 班、移時 {directionChanges.filter(r => r.action === "移時").length} 班。<a href="#/research?tab=dispatch">查看完整增減班與候車比較</a></p>}
        {day === "activity" && dispatchError && <p role="alert">活動班表快照載入失敗；下方不以一般表冒充模擬結果。</p>}
        <div className="research-time-range">
          <label>
            起始小時{" "}
            <input
              aria-label="起始小時"
              type="number"
              min={6}
              max={23}
              value={start}
              onChange={(e) => {
                const v = Math.min(23, Math.max(6, +e.target.value));
                setStart(v);
                setEnd((x) => Math.max(x, v + 1));
              }}
            />
          </label>
          <label>
            結束小時{" "}
            <input
              aria-label="結束小時"
              type="number"
              min={start + 1}
              max={26}
              value={end}
              onChange={(e) =>
                setEnd(Math.min(26, Math.max(start + 1, +e.target.value)))
              }
            />
          </label>
          <span>24＝次日 00 時 · 結束時刻不含 · 共 {greenEvent ? eventDepartures.length : departures.length} 班</span>
        </div>
        <div className="departure-grid">
          {greenEvent ? eventDepartures.map((r, i) => (
            <div className={`departure-${r.action === "增班" ? "added" : r.action === "移時" ? "retimed" : "kept"}`} key={`${r.new_beimen}-${i}`}>
              <strong className="num">{r.new_beimen.slice(0,5)}</strong>
              <span>{r.action}{r.action === "移時" ? ` · 原 ${r.old_beimen.slice(0,5)}` : ""} · {direction}</span>
            </div>
          )) : departures.map((r) => (
            <div key={r.departure_minute}>
              <strong className="num">
                {departureTime(+r.departure_minute)}
              </strong>
              <span>往{r.terminal}</span>
            </div>
          ))}
          {!(greenEvent ? eventDepartures.length : departures.length) && <p>{greenEvent && !dispatch ? "正在載入活動模擬班表…" : "此篩選範圍無班次。"}</p>}
        </div>
        {greenEvent && directionChanges.some(r => r.action === "取消") && <details>
          <summary>查看此方向取消的原班次</summary>
          <p>{directionChanges.filter(r => r.action === "取消").map(r => r.old_beimen.slice(0, 5)).join("、")}</p>
        </details>}
        <ChartNote>
          {greenEvent ? "新增與移時僅是模型建議；取消的班次不列在新班表。完整逐班變更與成本等待比較請見「增減班比較」。" : "保留原表終點與區間車差異；原表畫線的班次已依終點解析。活動日欄位不等於已取得當天實際營運班表。"}
        </ChartNote>
      </section>
      <section className="panel research-panel">
        <div className="research-panel-head">
          <div>
            <span className="eyebrow">TRAVEL AFTER DEPARTURE</span>
            <h2>候選發車站 → {station}</h2>
          </div>
          <Badge variant="outline">{LINES[line]}</Badge>
        </div>
        <p>
          只計「已發車後」區間行駛＋中途停靠。不是下令後多久能到，也不表示當時有可用備車。
        </p>
        <p className="mobile-table-hint">左右滑動查看完整表格</p>
        <div
          className="research-table-wrap"
          tabIndex={0}
          role="region"
          aria-label="可左右捲動的資料表"
        >
          <table>
            <thead>
              <tr>
                <th>候選發車站</th>
                <th>估計行車時間</th>
                <th>反向對稱假設</th>
                <th>路徑</th>
              </tr>
            </thead>
            <tbody>
              {paths.map((r) => (
                <tr key={r.origin}>
                  <td>{r.origin}</td>
                  <td>{format(+r.travel_seconds / 60)} 分</td>
                  <td>{+r.reverse_assumed ? "是" : "否"}</td>
                  <td className="path-cell">{r.path_text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ChartNote>
          完整提前量＝整備／司機就緒＋出庫接軌＋等待插車＋行車時間（必要時加折返）。前三項尚缺資料，沒有當作
          0。路段停靠欄與反向對稱仍需營運方確認。
        </ChartNote>
        <a
          href="https://data.taipei/dataset/detail?id=513e97fe-6a98-4a0d-b7dc-11122c8638d4"
          target="_blank"
          rel="noreferrer"
        >
          官方相鄰站行車資料 ↗
        </a>
      </section>
      <div className="research-explanation">
        <div>
          <h3>如何估計車上已有人數？</h3>
          <p>
            現行調度模型已用歷史 OD、假設路徑與班表逐站推估上、下車及剩餘空間；這是模擬值，仍需逐班載重或實載人數校準。四級擁擠度不能直接換成精確人數。
          </p>
        </div>
        <div>
          <h3>目前調度方案的界線</h3>
          <p>
            已比較有限候選的增班、取消與移時方案及其候車與成本；尚未核實備車、司機、出庫與折返可行性，也沒有全域最優保證。兩分鐘班距是研究假設，不是官方安全下限。
          </p>
        </div>
      </div>
      <a
        className="research-source"
        href="https://www.dorts.gov.taipei/News_Content.aspx?n=2A66A485FACB0D5B&s=F32662CFF6FE4D80"
        target="_blank"
        rel="noreferrer"
      >
        高運量列車容量參考：臺北市政府捷運工程局 ↗
      </a>
    </>
  );
}
