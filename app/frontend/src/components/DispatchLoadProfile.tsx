import {useEffect, useState} from "react";
import {Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {Download} from "lucide-react";
import {publicFile} from "@/lib/research";
import {selectLoadProfile, type DispatchLoadData, type DispatchLoadRow} from "@/lib/dispatch-load";
import "./DispatchLoadProfile.css";

const timeLabel = (value: number) => `${String(Math.floor(value/60)).padStart(2,"0")}:${String(Math.floor(value%60)).padStart(2,"0")}`;
const number = (value: number) => value.toLocaleString("zh-TW",{maximumFractionDigits:0});

export function DispatchLoadProfile({event}: {event:string}) {
  const [data,setData] = useState<DispatchLoadData>();
  const [failed,setFailed] = useState(false);
  const [plan,setPlan] = useState<DispatchLoadRow["plan"]>("baseline");
  useEffect(() => {
    const controller = new AbortController();
    fetch(publicFile("dispatch-load-profile.json"),{signal:controller.signal})
      .then(r => {if (!r.ok) throw new Error("missing"); return r.json();})
      .then((value:DispatchLoadData) => {if (value.schema_version!==1) throw new Error("schema"); setData(value);})
      .catch(() => {if (!controller.signal.aborted) setFailed(true);});
    return () => controller.abort();
  },[]);
  return <section className="dispatch-section dispatch-load-profile">
    <div className="dispatch-section-title"><h3>到北門時，還有多少空間</h3><span>OD 推估沿途上下車</span></div>
    <p>全線模型從起站開始累計載客，各站先下後上；到北門的剩餘空間隨方向、時刻與班表改變。這裡沒有把每列車固定設成 50% 空間。</p>
    <p>下車後可再載人數＝2,220 −（抵站前車上人數 − 北門下車人數）。舊「候車情境」頁的 50% 是敏感度假設，與本圖分開。</p>
    {!data ? <p role="status">{failed ? "動態載量明細暫時無法載入；不以固定 50% 或零人替代。" : "載入逐班載量模擬…"}</p> : <>
      <div className="dispatch-load-controls">
        <label className="research-filter">載量比較班表<select value={plan} onChange={e=>setPlan(e.target.value as DispatchLoadRow["plan"])}>
          <option value="baseline">原班表</option><option value="selected_lambda1">增減／移時方案（λ＝1）</option>
        </select></label>
        <span>此圖獨立比較原班表與 λ＝1；不隨上方其他 λ 改變。</span>
      </div>
      <div className="dispatch-load-charts">{["往松山","往新店"].map(direction=>{
        const rows=selectLoadProfile(data,event,plan,direction);
        const average=rows.length ? rows.reduce((sum,r)=>sum+r.remaining_capacity_mean,0)/rows.length : null;
        return <div key={direction} className="dispatch-load-direction">
          <h4>{direction}<span>{rows.length} 班{average!=null && ` · 每班平均剩餘 ${number(average)} 人`}</span></h4>
          {!rows.length ? <p>此場次未提供逐班載量，不套用其他場次。</p> : <div className="dispatch-load-chart" role="img" aria-label={`${direction}下客後剩餘容量百分比，均值與模擬情境5至95百分位`}>
            <ResponsiveContainer width="100%" height="100%"><ComposedChart data={rows} margin={{top:20,right:15,left:0,bottom:8}}>
              <CartesianGrid stroke="#303034" vertical={false}/>
              <XAxis dataKey="minute" type="number" domain={[1080,1380]} ticks={[1080,1140,1200,1260,1320,1380]} tickFormatter={timeLabel} stroke="#b6b6bd" tick={{fontSize:11}}/>
              <YAxis domain={[0,100]} unit="%" width={46} stroke="#b6b6bd" tick={{fontSize:11}}/>
              <Tooltip contentStyle={{background:"#202023",border:"1px solid #48484d",color:"#eee"}}
                labelFormatter={value=>timeLabel(Number(value))}
                formatter={(value,name)=>[Array.isArray(value) ? `${value.map(v=>Number(v).toFixed(1)).join("–")}%` : `${Number(value).toFixed(1)}%`,name]}/>
              <ReferenceLine y={50} stroke="#ad895e" strokeDasharray="5 5" label={{value:"固定 50% 示意",position:"insideBottomRight",fill:"#c5a986",fontSize:10}}/>
              <Area type="linear" dataKey="rangePercent" name="情境 5–95 百分位" fill="#96969c" fillOpacity={.2} stroke="none" isAnimationActive={false}/>
              <Line type="linear" dataKey="meanPercent" name="平均剩餘空間" stroke="#dededb" strokeWidth={2} dot={false} isAnimationActive={false}/>
            </ComposedChart></ResponsiveContainer>
          </div>}
        </div>;
      })}</div>
      <p className="dispatch-caption">白線為 {data.evaluation_draws} 次模擬的逐班平均，灰帶是情境第 5–95 百分位，並非實測載量或信賴區間。時段需求、選路與步行仍有假設；小時 OD 無法唯一還原每位乘客搭哪一班。</p>
      <a href={publicFile("dispatch-beimen_load_profile.csv")} download><Download size={14}/>下載逐班抵站、上下車與剩餘空間</a>
      <details className="dispatch-load-method"><summary>如何比固定比例更接近營運過程</summary>
        <p>以凍結需求預測及較早一般日 OD 分配起迄與方向，配合站間時間、轉乘及班表，逐站更新車上人數。取消前車時，未搭上的乘客會留待後車，不會讓後車憑空恢復一半空位。</p>
        <p>若能補到實際刷卡時間、列車運行紀錄或載重資料，才能校準分鐘到達、路徑選擇及載量。本版仍屬回顧模擬；歷史資料發布時點尚未全面核實。</p>
        {data.sources.map(s=><a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title}</a>)}
      </details>
    </>}
  </section>;
}
