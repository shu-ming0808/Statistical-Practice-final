import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { publicFile, type Research } from "@/lib/research";

interface ReplayEvent {
  event_date: string;
  predicted_demand: number;
  observed_demand: number;
  predicted_wait_person_minutes: number;
  hindsight_wait_person_minutes: number;
  wait_person_minutes_difference: number;
  predicted_max_queue: number;
  hindsight_max_queue: number;
  both_cleared: boolean;
}
interface Sensitivity {
  event_date: string;
  shape: "uniform" | "broad" | "concentrated";
  remaining_fraction: number;
  predicted_wait_person_minutes: number;
  hindsight_wait_person_minutes: number;
  both_cleared: boolean;
}
interface Replay {
  schema_version: number;
  events: ReplayEvent[];
  sensitivity: Sensitivity[];
}

const fmt = (value: number) => value.toLocaleString("zh-TW", { maximumFractionDigits: 0 });
const names = { uniform: "均勻", broad: "寬峰", concentrated: "集中峰" };
const tip = { background: "#202023", border: "1px solid #48484d", color: "#eee" };

export function DecisionReplay({ data, event }: { data: Research; event: string }) {
  const [replay, setReplay] = useState<Replay>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch(publicFile("queue-replay.json"), { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error("回顧檢查尚未發布");
        return response.json();
      })
      .then((value: Replay) => {
        if (value.schema_version !== 1 || value.events.length !== 9) throw new Error("回顧檢查版本不符");
        setReplay(value);
      })
      .catch(cause => { if (!controller.signal.aborted) setError(String(cause)); });
    return () => controller.abort();
  }, []);
  if (error) return <p role="alert">{error}</p>;
  if (!replay) return <p aria-busy="true">正在載入回顧檢查…</p>;
  const selected = replay.events.find(row => row.event_date === event);
  if (!selected) return <p>這場沒有相同條件的回顧檢查。</p>;
  const hourly = data.model.hourly.filter(row => row.event_date === event).map(row => ({
    hour: `${row.hour}:00`, predicted: row.predicted, actual: row.actual,
  }));
  const sensitivity = replay.sensitivity.filter(row => row.event_date === event && row.remaining_fraction === 0.5)
    .map(row => ({ ...row, label: names[row.shape] }));
  return <>
    <div className="research-callout">
      <div><strong>{event}｜從預測到候車的單場案例</strong>
        <p>同一份一般班表與相同的分鐘形狀、方向比例及列車空位。藍色是按活動前一天輸入規則建立的封存預測；橘色只作事後檢查，不回填模型或重新選方案。歷史資料的實際發布時點尚未完整核實。</p>
      </div>
    </div>
    <div className="research-stats">
      <div className="research-stat"><span>18–23 時預測／實際</span><div><strong>{fmt(selected.predicted_demand)}</strong><small>／ {fmt(selected.observed_demand)} 人次</small></div><p>同一批北門 OD 小時格</p></div>
      <div className="research-stat"><span>預測情境候車</span><div><strong>{fmt(selected.predicted_wait_person_minutes)}</strong><small>人分鐘</small></div><p>寬峰、雙向各半、每班剩餘 1,110 人</p></div>
      <div className="research-stat"><span>事後人數重播差額</span><div><strong>{selected.wait_person_minutes_difference > 0 ? "+" : ""}{fmt(selected.wait_person_minutes_difference)}</strong><small>人分鐘</small></div><p>同條件下的候車模型差，不是實測等待</p></div>
    </div>
    <section className="panel research-panel">
      <h2>01　按活動前一天資訊預測逐時人數</h2>
      <p>實際值只在活動後用來量化預測誤差。</p>
      <div className="research-chart small"><ResponsiveContainer width="100%" height="100%"><BarChart data={hourly}>
        <CartesianGrid vertical={false} stroke="#303034"/><XAxis dataKey="hour"/><YAxis width={56}/><Tooltip contentStyle={tip}/><Legend/>
        <Bar dataKey="predicted" name="事前預測" fill="#8194aa" isAnimationActive={false}/>
        <Bar dataKey="actual" name="事後實際" fill="#c17e5e" isAnimationActive={false}/>
      </BarChart></ResponsiveContainer></div>
    </section>
    <section className="panel research-panel">
      <h2>02　分鐘到達與候車重播</h2>
      <p>逐時人數拆成分鐘是假設，沒有分鐘刷卡真值。下圖固定 50% 列車空位，比較三種同小時人數但不同集中程度的到達形狀。</p>
      <div className="research-chart small"><ResponsiveContainer width="100%" height="100%"><LineChart data={sensitivity}>
        <CartesianGrid vertical={false} stroke="#303034"/><XAxis dataKey="label"/><YAxis width={58}/><Tooltip contentStyle={tip} formatter={value => `${fmt(Number(value))} 人分鐘`}/><Legend/>
        <Line dataKey="predicted_wait_person_minutes" name="預測情境" stroke="#8194aa" strokeWidth={2} isAnimationActive={false}/>
        <Line dataKey="hindsight_wait_person_minutes" name="事後人數重播" stroke="#c17e5e" strokeWidth={2} isAnimationActive={false}/>
      </LineChart></ResponsiveContainer></div>
      <p className="research-note">同一分鐘形狀及班表下，只更換每小時人數。最大單方向候車：預測 {fmt(selected.predicted_max_queue)} 人，事後重播 {fmt(selected.hindsight_max_queue)} 人；兩者{selected.both_cleared ? "都已在模擬窗口清空" : "至少一者未在模擬窗口清空"}。</p>
    </section>
    <section className="panel research-panel">
      <h2>03　班次選擇與成本取捨</h2>
      <p>以事前預測選班表，應再用事後真值及實際列車載量驗證。現有全線增減班方案已比較可變支出與模型候車，但這張回顧圖只重播北門固定班表，不能聲稱全線調度已被實際人流驗證。</p>
      <a href="#/research?tab=dispatch">查看同場的原班表、減班試算與加權方案 →</a>
    </section>
    <p className="research-note">九場已反覆檢視。所用班表是 2026/8/30 起的一般表，並非歷史活動當日的實際服務；50% 空位也是假設。<a href="https://github.com/shu-ming0808/Statistical-Practice-final/blob/main/result/queue_replay/report.md" target="_blank" rel="noreferrer">完整方法與逐場結果</a></p>
  </>;
}
