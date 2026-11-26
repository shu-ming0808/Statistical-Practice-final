import test from 'node:test';
import assert from 'node:assert/strict';
import {minuteFlows, minuteWeight, densityColor} from '../src/lib/minute-flow.ts';

const row=(station,hour,count,complete=true)=>({
  station,hour_start:`2026-08-15T${hour}:00:00`,origin_count:count,origin_complete:complete,
});

test('minute scenario conserves the observed hourly total',()=>{
  for(const peak of [false,true]){
    const total=Array.from({length:60},(_,m)=>minuteWeight(m,peak)).reduce((a,b)=>a+b,0);
    assert.ok(Math.abs(total-1)<1e-12);
  }
  const rows=[row('北門','20',600),row('北門','21',1200),row('雙連','21',300)];
  const peak=minuteFlows(rows,3*60+29);
  const shoulder=minuteFlows(rows,2*60+29);
  assert.ok(peak['北門'].count>shoulder['北門'].count);
  assert.ok(peak['北門'].intensity>peak['雙連'].intensity);
});

test('missing minute stays unknown and density runs blue to red',()=>{
  const flows=minuteFlows([row('北門','21',null,false),row('雙連','21',600)],3*60+29);
  assert.equal(flows['北門'].count,null);
  assert.equal(flows['北門'].color,'#71717a');
  assert.equal(densityColor(0),'rgb(59, 130, 246)');
  assert.equal(densityColor(1),'rgb(239, 68, 68)');
});
