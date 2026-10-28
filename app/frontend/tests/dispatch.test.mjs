import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dispatchSlotLabel, selectDispatch} from '../src/lib/dispatch.ts';
const data=JSON.parse(readFileSync(new URL('../public/data/dispatch.json',import.meta.url),'utf8'));

test('each event/weight resolves its own comparison, four stations, and timetable',()=>{
  for(const row of data.comparison){
    const selected=selectDispatch(data,row.event_date,row.waiting_weight);
    assert.equal(selected.comparison,row);
    assert.equal(selected.stations.length,4);
    assert.ok(selected.timetable.length);
    assert.ok(selected.timetable.every(r=>r.event_date===row.event_date && r.waiting_weight===row.waiting_weight));
    assert.equal(selected.timetable.filter(r=>r.action==='增班').length,row.added);
    assert.equal(selected.timetable.filter(r=>r.action==='減班').length,row.removed);
    assert.equal(selected.timetable.filter(r=>r.action==='移時').length,row.retimed);
  }
});
test('missing event is not silently replaced by another event or zero',()=>{
  const selected=selectDispatch(data,'1900-01-01',1);
  assert.equal(selected.comparison,undefined);
  assert.deepEqual(selected.stations,[]);
  assert.deepEqual(selected.slots,[]);
  assert.equal(selected.candidateComparison,undefined);
});

test('15-minute heatmap is complete and reconciles with actual departure times',()=>{
  const minutes=value=>{
    if(!value)return null;
    const [h,m,s=0]=value.split(':').map(Number);
    return h*60+m+s/60;
  };
  for(const row of data.comparison){
    const selected=selectDispatch(data,row.event_date,row.waiting_weight);
    assert.equal(selected.slots.length,40);
    assert.equal(new Set(selected.slots.map(s=>`${s.direction}-${s.slot_start}`)).size,40);
    for(const direction of ['往松山','往新店']){
      const slots=selected.slots.filter(s=>s.direction===direction).sort((a,b)=>a.slot_start.localeCompare(b.slot_start));
      assert.equal(slots.length,20);
      assert.equal(slots[0].slot_start,'18:00');
      assert.equal(slots.at(-1).slot_end,'23:00');
      for(const [index,slot] of slots.entries()){
        const start=minutes(slot.slot_start),end=minutes(slot.slot_end);
        assert.equal(end-start,15);
        if(index)assert.equal(slot.slot_start,slots[index-1].slot_end);
        const count=field=>selected.timetable.filter(t=>t.direction===direction).reduce((n,t)=>{
          const time=minutes(t[field]);return n+(time!==null&&time>=start&&time<end?1:0);
        },0);
        assert.equal(slot.baseline_count,count('old_beimen'));
        assert.equal(slot.optimized_count,count('new_beimen'));
        assert.equal(slot.net_change,slot.optimized_count-slot.baseline_count);
      }
    }
    assert.equal(selected.slots.reduce((s,r)=>s+r.baseline_count,0),row.baseline_services);
    assert.equal(selected.slots.reduce((s,r)=>s+r.optimized_count,0),row.optimized_services);
    assert.equal(selected.slots.reduce((s,r)=>s+r.net_change,0),row.added-row.removed);
  }
});

test('a departure moved across a slot boundary creates opposite bin deltas without adding or cancelling a train',()=>{
  const fixture={...data,comparison:[{event_date:'2024-08-10',waiting_weight:1,added:0,removed:0,retimed:1}],
    timetable:[{event_date:'2024-08-10',waiting_weight:1,direction:'往松山',action:'移時',old_beimen:'18:14:00',new_beimen:'18:16:00'}],
    slot_counts:[
      {event_date:'2024-08-10',waiting_weight:1,direction:'往松山',slot_start:'18:00',slot_end:'18:15',baseline_count:1,optimized_count:0,net_change:-1},
      {event_date:'2024-08-10',waiting_weight:1,direction:'往松山',slot_start:'18:15',slot_end:'18:30',baseline_count:0,optimized_count:1,net_change:1},
      {event_date:'2024-08-10',waiting_weight:2,direction:'往松山',slot_start:'18:00',slot_end:'18:15',baseline_count:9,optimized_count:9,net_change:0},
    ]};
  const selected=selectDispatch(fixture,'2024-08-10',1);
  assert.equal(selected.slots.length,2);
  assert.equal(selected.slots.reduce((n,s)=>n+s.net_change,0),0);
  assert.equal(selected.comparison.added,0);
  assert.equal(selected.comparison.removed,0);
  assert.equal(selected.timetable[0].action,'移時');
  assert.match(dispatchSlotLabel(selected.slots[0]),/原班表 1 班，新班表 0 班，時段淨變化 減少 1 班/);
  assert.match(dispatchSlotLabel(selected.slots[1]),/增加 1 班/);
});

test('search-family comparison belongs to the same event and weight and conserves service totals',()=>{
  assert.equal(data.candidate_family_comparison.length,data.comparison.length);
  for(const row of data.comparison){
    const selected=selectDispatch(data,row.event_date,row.waiting_weight);
    const c=selected.candidateComparison;
    assert.equal(c.event_date,row.event_date);
    assert.equal(c.waiting_weight,row.waiting_weight);
    assert.equal(c.selected_by_training,true);
    assert.equal(c.expanded_services,row.optimized_services);
    for(const prefix of ['original','expanded']){
      assert.equal(c[`${prefix}_services`],row.baseline_services+c[`${prefix}_added`]-c[`${prefix}_removed`]);
      assert.ok(c[`${prefix}_max_train_load`]<=2220+1e-7);
      const headway=c[`${prefix}_min_changed_headway_minutes`];
      assert.ok(headway===null||headway>=2-1e-7);
    }
    assert.ok(Math.abs(c.expanded_operating_change_twd-row.operating_change_twd)<1e-4);
  }
});
test('weight totals use the same eight events, not mixed comparisons',()=>{
  for(const total of data.summary){
    const rows=data.comparison.filter(r=>r.waiting_weight===total.waiting_weight);
    assert.equal(rows.length,8);
    assert.equal(rows.reduce((s,r)=>s+r.added,0),total.added);
    assert.equal(rows.reduce((s,r)=>s+r.removed,0),total.removed);
    assert.ok(Math.abs(rows.reduce((s,r)=>s+r.operating_change_twd,0)-total.operating_change_twd)<1e-4);
  }
});
