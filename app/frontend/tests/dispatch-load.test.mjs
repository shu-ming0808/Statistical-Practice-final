import test from 'node:test';
import assert from 'node:assert/strict';
import {selectLoadProfile} from '../src/lib/dispatch-load.ts';

const make=(more={})=>({event_date:'2024-08-10',plan:'baseline',direction:'往松山',departure_time:'20:30:00',within_decision_window:true,capacity_persons:2220,remaining_capacity_mean:1300,remaining_capacity_p05:900,remaining_capacity_p95:1700,...more});
test('load profile keeps event, plan and direction separate and uses after-alighting residual capacity',()=>{
 const data={rows:[make(),make({event_date:'2025-08-30'}),make({plan:'selected_lambda1'}),make({direction:'往新店'}),make({departure_time:'20:15:00'})]};
 const rows=selectLoadProfile(data,'2024-08-10','baseline','往松山');
 assert.equal(rows.length,2);assert.equal(rows[0].minute,1215);assert.equal(rows[1].meanPercent,1300/2220*100);
 assert.deepEqual(rows[1].rangePercent,[900/2220*100,1700/2220*100]);
 assert.deepEqual(selectLoadProfile(data,'1900-01-01','baseline','往松山'),[]);
});
test('load profile rejects impossible capacity or invalid published clock instead of showing 50 percent',()=>{
 for(const extra of [{remaining_capacity_mean:3000},{remaining_capacity_p05:-5},{remaining_capacity_p05:1800},{departure_time:'23:00:00'}]) {
  assert.throws(()=>selectLoadProfile({rows:[make(extra)]},'2024-08-10','baseline','往松山'));
 }
});
