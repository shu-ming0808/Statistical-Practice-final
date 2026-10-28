import {useEffect, useState} from "react";
import {Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {Download, RefreshCw, ArrowUpRight} from "lucide-react";
import {Button} from "@/components/ui/button";
import {publicFile} from "@/lib/research";
import {dispatchSlotLabel, selectDispatch, type DispatchData, type DispatchSlotCount} from "@/lib/dispatch";
import "./DispatchComparison.css";

const fmt = (v: number | null | undefined, digits = 0) => v == null ? "—" :
  v.toLocaleString("zh-TW", {maximumFractionDigits: digits});
const signed = (v: number, digits = 0) => `${v > 0 ? "+" : ""}${fmt(v, digits)}`;
const report = "https://github.com/shu-ming0808/Statistical-Practice-final/blob/main/result/green_dispatch/report.md";

function DispatchSlotHeatmap({slots}: {slots: DispatchSlotCount[]}) {
  const [activeKey, setActiveKey] = useState("");
  const keyOf = (slot: DispatchSlotCount) => `${slot.direction}-${slot.slot_start}`;
  const starts = [...new Set(slots.map(s => s.slot_start))].sort();
  const active = slots.find(s => keyOf(s) === activeKey) ?? slots[0];
  const directions = ["往松山", "往新店"] as const;
  return <section className="dispatch-section dispatch-slots">
    <div className="dispatch-section-title"><h3 id="dispatch-slots-heading">班次往哪個時段集中</h3><span>每格 15 分鐘</span></div>
    <p>比較同一時段的新舊班數。橘色表示較多、藍色表示較少；點選色塊查看數字。這是北門離站時刻的分布，15 分鐘不是發車間隔。</p>
    {!active ? <p className="dispatch-slot-empty">這組條件尚無時段班數資料，不以 0 代替。</p> : <>
      <div className="dispatch-slot-legend" aria-label="時段淨變化色彩圖例">
        <span><i className="slot-legend-more"/>班數增加</span><span><i className="slot-legend-fewer"/>班數減少</span><span><i className="slot-legend-same"/>班數相同</span>
        <small>每格：淨變化／原 → 新</small>
      </div>
      <div className="dispatch-heatmap-scroll" tabIndex={0} aria-label="15 分鐘班數熱圖，可水平捲動">
        <table className="dispatch-heatmap" aria-labelledby="dispatch-slots-heading">
          <thead><tr><th scope="col" className="dispatch-direction">行車方向</th>{starts.map(start => <th scope="col" key={start}>{start}</th>)}</tr></thead>
          <tbody>{directions.map(direction => <tr key={direction}>
            <th scope="row" className="dispatch-direction">{direction}</th>
            {starts.map(start => {
              const slot = slots.find(s => s.direction === direction && s.slot_start === start);
              if (!slot) return <td key={start} className="dispatch-slot-missing" aria-label={`${direction} ${start} 缺少資料`}>—</td>;
              const tone = slot.net_change > 0 ? "more" : slot.net_change < 0 ? "fewer" : "same";
              return <td key={start}><button type="button" className={`dispatch-slot-cell slot-${tone} slot-intensity-${Math.min(Math.abs(slot.net_change), 3)}`}
                aria-label={dispatchSlotLabel(slot)} aria-pressed={keyOf(slot) === keyOf(active)}
                title={dispatchSlotLabel(slot)} onClick={() => setActiveKey(keyOf(slot))}>
                <strong>{signed(slot.net_change)}</strong><span>{slot.baseline_count} → {slot.optimized_count}</span>
              </button></td>;
            })}
          </tr>)}</tbody>
        </table>
      </div>
      <div className="dispatch-slot-detail" aria-live="polite" aria-atomic="true">
        <strong>{active.direction} · {active.slot_start}–{active.slot_end}</strong>
        <span>原班表 <b>{active.baseline_count}</b> 班</span><span>新班表 <b>{active.optimized_count}</b> 班</span>
        <span>時段淨變化 <b>{signed(active.net_change)}</b> 班</span>
      </div>
      <p className="dispatch-slot-note">移時跨過 15 分鐘邊界，也會讓一格減少、另一格增加；所以格內差值不能當成實際增班／減班數。實際動作請看下方逐班對照。</p>
      <a href={publicFile("dispatch-slot_counts.csv")} download><Download size={14}/>下載各場 15 分鐘班數 CSV</a>
    </>}
  </section>;
}

export function DispatchComparison() {
  const [data, setData] = useState<DispatchData>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [event, setEvent] = useState("2024-08-10");
  const [weight, setWeight] = useState(1);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    fetch(publicFile("dispatch.json"), {signal: abort.signal})
      .then(r => {
        if (!r.ok || !r.headers.get("content-type")?.includes("application/json"))
          throw new Error("班表成果尚未載入，請重試或查看完整報告。");
        return r.json();
      })
      .then((value: DispatchData) => {
        if (value.schema_version !== 1 || !value.no_wait_threshold) throw new Error("成果格式不符，請查看完整報告。");
        setData(value);
      }).catch(e => {if (!abort.signal.aborted) setError(e.message);});
    return () => abort.abort();
  }, [reload]);
  if (error) return <div className="empty-state" role="alert"><p>{error}</p>
    <Button variant="outline" onClick={() => {setError(""); setReload(n => n + 1);}}><RefreshCw size={16}/>重試</Button>
    <a href={report} target="_blank" rel="noreferrer">完整報告</a></div>;
  if (!data) return <div className="empty-state" aria-busy="true">正在載入增減班比較…</div>;
  const selected = selectDispatch(data, event, weight);
  const r = selected.comparison;
  const candidate = selected.candidateComparison;
  const dates = [...new Set(data.comparison.map(v => v.event_date))];
  const changes = selected.timetable.filter(v => showAll || v.action !== "保留");
  return <div className="dispatch-results">
    <header className="dispatch-heading">
      <div><span className="dispatch-eyebrow">松山新店線 · 18:00–23:00</span>
        <h2>把班次放在人潮需要的時間</h2>
        <p>允許增班、減班與移時，紅／橘線維持原班表。以下為模擬比較，尚未納入完整備車、司機與折返限制。</p></div>
      <a href={report} target="_blank" rel="noreferrer">方法與完整結果 <ArrowUpRight size={15}/></a>
    </header>
    <div className="dispatch-controls">
      <label className="research-filter">活動日期<select value={event} onChange={e => setEvent(e.target.value)}>
        {dates.map(d => <option key={d}>{d}</option>)}</select></label>
      <label className="research-filter">候車成本權重 λ<select value={weight} onChange={e => setWeight(+e.target.value)}>
        {data.waiting_weights.map(w => <option key={w} value={w}>{w}{w === 1 ? " · 參考設定" : ""}</option>)}</select></label>
      <p>λ 越高，候車時間的成本估值越高。這是偏好設定，不是模型學出的權重。</p>
    </div>
    {!r ? <div className="empty-state">這一場尚無可比較結果，不以 0 代替缺值。</div> : <>
      <div className="dispatch-metrics" aria-live="polite">
        <div><span>兩方向服務班數</span><strong>{r.baseline_services} <small>→</small> {r.optimized_services}</strong>
          <p>增 {r.added} 班 · 減 {r.removed} 班 · 移時 {r.retimed} 班</p></div>
        <div><span>可變營運成本差</span><strong>{signed(r.operating_change_twd)} <small>元</small></strong>
          <p>原 {fmt(r.baseline_operating_twd)} → 新 {fmt(r.optimized_operating_twd)} 元</p></div>
        <div><span>總候車人分鐘變化</span><strong>{signed(r.wait_change_percent, 1)} <small>%</small></strong>
          <p>平均 {fmt(r.baseline_mean_wait, 2)} → {fmt(r.optimized_mean_wait, 2)} 分鐘</p></div>
      </div>
      <p className="dispatch-caption">成本是可避免的營運支出代理，包含新增列車的假設空車回送。平均等待涵蓋綠線一般背景旅次及可推導紅／橘線轉乘，並非北門散場尖峰平均，也不是全網實測。</p>
      {(r.unserved_mean > 0 || r.baseline_unserved_mean > 0) && <p role="status">部分旅次在共同終點尚未完成，所列平均等待只累計至該終點，不能當成完整候車時間；P95 留空。</p>}
      <DispatchSlotHeatmap slots={selected.slots}/>
      {candidate && <section className="dispatch-section">
        <div className="dispatch-section-title"><h3>加入 15 分鐘候選，有沒有更合適</h3><span>同一場、同一個 λ</span></div>
        <p>「原搜尋」是原本的班表搜尋結果。兩套搜尋使用相同人流情境；擴充搜尋保留原有候選，再加入分時配置，依訓練情境的成本與等待選擇。下表呈現獨立模擬評估。</p>
        <div className="dispatch-table-wrap"><table><thead><tr><th>比較項目</th><th>原搜尋</th><th>納入 15 分鐘候選</th></tr></thead><tbody>
          <tr><td>服務班數</td><td>{candidate.original_services}</td><td>{candidate.expanded_services}</td></tr>
          <tr><td>實際增／減／移時</td><td>+{candidate.original_added}／−{candidate.original_removed}／{candidate.original_retimed}</td><td>+{candidate.expanded_added}／−{candidate.expanded_removed}／{candidate.expanded_retimed}</td></tr>
          <tr><td>相對原班表的可變成本差</td><td>{signed(candidate.original_operating_change_twd)} 元</td><td>{signed(candidate.expanded_operating_change_twd)} 元</td></tr>
          <tr><td>平均候車</td><td>{fmt(candidate.original_mean_wait, 2)} 分</td><td>{fmt(candidate.expanded_mean_wait, 2)} 分</td></tr>
          <tr><td>評估期末未完成旅次</td><td>{fmt(candidate.original_unserved_mean, 2)} 人／情境</td><td>{fmt(candidate.expanded_unserved_mean, 2)} 人／情境</td></tr>
          <tr><td>評估情境最大列車載量</td><td>{fmt(candidate.original_max_train_load)}／2,220 人</td><td>{fmt(candidate.expanded_max_train_load)}／2,220 人</td></tr>
          <tr><td>涉及調整班次的最短同向班距</td><td>{candidate.original_min_changed_headway_minutes == null ? "沒有調整班次" : `${fmt(candidate.original_min_changed_headway_minutes, 2)} 分`}</td><td>{candidate.expanded_min_changed_headway_minutes == null ? "沒有調整班次" : `${fmt(candidate.expanded_min_changed_headway_minutes, 2)} 分`}</td></tr>
        </tbody></table></div>
        <p>班距檢查包含窗口外的相鄰原班；120 秒是本研究設定。加入候選不保證會採用新配置，也不保證獨立評估的等待與支出同時下降。</p>
        <a href={publicFile("dispatch-candidate_family_comparison.csv")} download><Download size={14}/>下載搜尋方式比較 CSV</a>
      </section>}
      <section className="dispatch-section">
        <div className="dispatch-section-title"><h3>原四站，轉乘也一起看</h3><span>只比較涉及綠線的旅次</span></div>
        <p>不經綠線的旅次未調整；大橋頭、雙連、民權西路沒有直接減班。出發站優先歸屬，避免同一旅次重複計算。</p>
        <div className="dispatch-chart" role="img" aria-label="四站相關旅次原班表及新班表平均候車時間比較">
          <ResponsiveContainer width="100%" height="100%"><BarChart data={selected.stations} margin={{top: 12, right: 16, left: 0, bottom: 8}}>
            <CartesianGrid stroke="#303034" vertical={false}/><XAxis dataKey="station" stroke="#b6b6bd" tick={{fontSize: 12}}/>
            <YAxis stroke="#b6b6bd" width={44} tick={{fontSize: 12}} unit="分"/>
            <Tooltip contentStyle={{background: "#202023", border: "1px solid #48484d", color: "#eee"}} formatter={value => `${fmt(Number(value), 2)} 分`}/>
            <Legend/><Bar dataKey="baseline_mean_wait" name="原班表" fill="#727278" radius={[3,3,0,0]}/>
            <Bar dataKey="optimized_mean_wait" name="增減／移時" fill="#dededb" radius={[3,3,0,0]}/>
          </BarChart></ResponsiveContainer>
        </div>
        <div className="dispatch-table-wrap"><table><thead><tr><th>車站相關旅次</th><th>原平均候車</th><th>新平均候車</th><th>總變化</th><th>其中紅／橘線段變化</th></tr></thead>
          <tbody>{selected.stations.map(s => <tr key={s.station}><td>{s.station}</td><td>{fmt(s.baseline_mean_wait,2)} 分</td><td>{fmt(s.optimized_mean_wait,2)} 分</td>
            <td>{s.baseline_mean_wait == null || s.optimized_mean_wait == null ? "—" : `${signed(s.optimized_mean_wait-s.baseline_mean_wait,2)} 分`}</td>
            <td>{s.baseline_mean_wait == null || s.optimized_mean_wait == null || s.baseline_green_wait == null || s.optimized_green_wait == null ? "—" : `${signed((s.optimized_mean_wait-s.optimized_green_wait)-(s.baseline_mean_wait-s.baseline_green_wait),2)} 分`}</td></tr>)}</tbody></table></div>
        <p>紅／橘線段包含接入綠線前的候車及可推導的後續轉乘等待；班表固定，但改變綠線到站時間仍可能影響接車。</p>
        <a href={publicFile("dispatch-station_impacts.csv")} download><Download size={14}/>下載四站與轉乘拆分 CSV</a>
      </section>
      <section className="dispatch-section">
        <div className="dispatch-section-title"><h3>這一場，哪些班次改了</h3>
          <Button variant="outline" size="sm" onClick={() => setShowAll(!showAll)}>{showAll ? "只看變更" : "包含保留班次"}</Button></div>
        <p>調整窗口以北門離站為準。起站時刻由官方站間時間回推，尚未包含出庫準備與折返安排。目前顯示 {changes.length} 筆。</p>
        <div className="dispatch-table-wrap dispatch-timetable" tabIndex={0} aria-label="可捲動的逐班時刻對照"><table><thead><tr><th>動作</th><th>方向</th><th>北門原離站</th><th>北門新離站</th><th>起站</th><th>新起站離站</th></tr></thead>
          <tbody>{changes.map((t,i) => <tr key={`${t.direction}-${t.old_beimen}-${t.new_beimen}-${i}`}><td><span className={`dispatch-action action-${t.action}`}>{t.action}</span></td><td>{t.direction}</td>
            <td>{t.old_beimen || "—"}</td><td>{t.new_beimen || "—"}</td><td>{t.origin}</td><td>{t.new_origin || "—"}</td></tr>)}</tbody></table></div>
        {!changes.length && <p>這組條件下保留原班表。</p>}
        <a href={publicFile("dispatch-timetable.csv")} download><Download size={14}/>下載所有場次班表變更 CSV</a>
      </section>
      <section className="dispatch-section">
        <h3>候車尾端與成本估值</h3>
        <div className="dispatch-table-wrap"><table><thead><tr><th>指標</th><th>原班表</th><th>新班表</th></tr></thead><tbody>
          <tr><td>混合模擬旅次 P95（分箱近似）</td><td>{fmt(r.baseline_p95,1)} 分</td><td>{fmt(r.optimized_p95,1)} 分</td></tr>
          <tr><td>評估期末未完成旅次</td><td>{fmt(r.baseline_unserved_mean,2)} 人／情境</td><td>{fmt(r.unserved_mean,2)} 人／情境</td></tr>
        </tbody></table></div>
        <p>加權總成本差：{signed(r.evaluation_total_cost_change)} 元（含乘客時間估值，不能當成營運現金節省）。P95 僅供描述，沒有設定 10 分鐘或 5% 的合格門檻。</p>
      </section>
    </>}
    <section className="dispatch-section"><h3>八場活動的權重比較</h3>
      <p>不同 λ 代表不同時間估值，不能直接用加權總成本跨 λ 選「最好」的一個。</p>
      <div className="dispatch-table-wrap"><table><thead><tr><th>λ</th><th>增／減／移時</th><th>可變成本差</th><th>總等待變化</th></tr></thead>
        <tbody>{data.summary.map(s => <tr key={s.waiting_weight}><td>{s.waiting_weight}</td><td>+{s.added}／−{s.removed}／{s.retimed}</td><td>{signed(s.operating_change_twd)} 元</td><td>{signed(s.wait_change_percent,1)}%</td></tr>)}</tbody></table></div>
    </section>
    <details className="dispatch-assumptions"><summary>資料、假設與適用範圍</summary><ul>{data.assumptions.map(a => <li key={a}>{a}</li>)}</ul>
      {data.sources.map(s => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title}<ArrowUpRight size={13}/></a>)}</details>
    <footer className="dispatch-footer"><span>調度成果更新：{new Date(data.generated_at).toLocaleString("zh-TW", {timeZone: "Asia/Taipei", hour12: false})}</span>
      <a href={publicFile("dispatch-comparison.csv")} download><Download size={14}/>下載比較數據</a></footer>
  </div>;
}
