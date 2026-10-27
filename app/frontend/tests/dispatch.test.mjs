import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {selectDispatch} from '../src/lib/dispatch.ts';
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
