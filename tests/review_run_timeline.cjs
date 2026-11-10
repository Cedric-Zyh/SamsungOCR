const assert = require('node:assert/strict');
const test = require('node:test');
const {resolveRunTimeline,renderRunTimeline,timelineSummary} = require('../static/modules/review/run_timeline.mjs');

const origin = Date.parse('2026-11-09T10:00:00Z');
const stamp = seconds => new Date(origin + seconds * 1000).toISOString();
const span = (id,lane,start,end,extra={}) => ({id,lane,label:id,started_at:stamp(start),
  finished_at:stamp(end),seconds:end-start,status:'succeeded',kind:'recognition',...extra});
const recorded = (spans, extra={}) => ({version:1,elapsed_seconds:20,started_at:stamp(0),finished_at:stamp(20),
  runs:[{id:'current',kind:'current',started_at:stamp(0),finished_at:stamp(20),elapsed_seconds:20,spans}],...extra});
const remote = (id,scope,start,end,extra={}) => ({id,method:'danzhengtong',request_scope:scope,
  action:scope==='page'?'整页单证识别':scope==='seal'?'印章专用场景识别':'识别',status:'succeeded',
  transport:{started_at:stamp(start),finished_at:stamp(end),elapsed_seconds:end-start},...extra});

test('page and multiple stamp lanes share one axis with each physical request still visible', () => {
  const data = {timeline:recorded([
    span('page','danzhengtong_page',0,12,{kind:'lifecycle',label:'整页上传至结果'}),
    span('page-upload','danzhengtong_page',0,2,{kind:'request'}),
    span('stamp-1','danzhengtong_seal',3,18,{kind:'lifecycle',label:'第1枚 · 上传至结果'}),
    span('stamp-2','danzhengtong_seal',4,19,{kind:'lifecycle',label:'第2枚 · 上传至结果'}),
    span('stamp-wait','danzhengtong_seal',7,15,{kind:'wait',label:'第1枚 · 等待 / 调度间隔'}),
  ])};
  const html = renderRunTimeline(data);
  assert.equal((html.match(/class="run-timeline-axis"/g)||[]).length,1);
  assert.equal((html.match(/class="run-timeline-lane"/g)||[]).length,2);
  assert.match(html,/单证通整页/);
  assert.match(html,/单证通印章/);
  assert.match(html,/第1枚/);
  assert.match(html,/第2枚/);
  assert.match(html,/left:15.00000%;width:75.00000%/);
  assert.match(html,/left:20.00000%;width:75.00000%/);
  assert.equal(timelineSummary(data).seconds,20);
});

test('old combined lanes use physical scope and propagate it to request and waiting children', () => {
  const data = {calls:[remote(1,'page',0,10),remote(2,'seal',1,12)],timeline:recorded([
    span('page','danzhengtong',0,10,{kind:'lifecycle',call_id:1}),
    span('page-upload','danzhengtong',0,1,{kind:'request',parent_id:'page'}),
    span('page-wait','danzhengtong',1,9,{kind:'wait',parent_id:'page'}),
    span('seal','danzhengtong',1,12,{kind:'lifecycle',call_id:2}),
    span('seal-upload','danzhengtong',1,2,{kind:'request',parent_id:'seal'}),
  ])};
  const before = structuredClone(data);
  const spans = resolveRunTimeline(data).runs[0].spans;
  assert.deepEqual(spans.map(item=>item.lane),[
    'danzhengtong_page','danzhengtong_page','danzhengtong_page','danzhengtong_seal','danzhengtong_seal']);
  assert.equal(spans[2].started_at,stamp(1));
  assert.equal(spans[2].finished_at,stamp(9));
  assert.deepEqual(data,before);
});

test('unknown or conflicting historical scope remains unclassified instead of borrowing stage meaning', () => {
  const data = {calls:[remote(1,'page',0,10),remote(1,'seal',1,12),
    remote(3,undefined,0,10,{stage:'seal',action:'识别'})],timeline:recorded([
      span('ambiguous','danzhengtong',0,10,{call_id:1}),
      span('stage-only','danzhengtong',0,10,{call_id:3}),
    ])};
  assert.deepEqual(resolveRunTimeline(data).runs[0].spans.map(item=>item.lane),['danzhengtong','danzhengtong']);
  assert.match(renderRunTimeline(data),/单证通（未分类）/);
  assert.doesNotMatch(renderRunTimeline(data),/单证通整页|单证通印章/);
});

test('explicit span scope wins and a cyclic old parent link cannot recurse indefinitely', () => {
  const data = {calls:[remote(1,'page',0,10)],timeline:recorded([
    span('explicit','danzhengtong',0,10,{call_id:1,request_scope:'seal'}),
    span('cycle-a','danzhengtong',0,1,{parent_id:'cycle-b'}),
    span('cycle-b','danzhengtong',1,2,{parent_id:'cycle-a'}),
  ])};
  assert.deepEqual(resolveRunTimeline(data).runs[0].spans.map(item=>item.lane),['danzhengtong_seal','danzhengtong','danzhengtong']);
});

test('transport-only legacy page and seal evidence separates rows without inventing internal phases', () => {
  const data = {calls:[remote(1,'page',0,10),remote(2,'seal',1,12)]};
  const runs = resolveRunTimeline(data).runs;
  assert.equal(runs.length,1);
  assert.deepEqual(runs[0].spans.map(item=>item.lane),['danzhengtong_page','danzhengtong_seal']);
  const html = renderRunTimeline(data);
  assert.equal((html.match(/class="run-timeline-axis"/g)||[]).length,1);
  assert.doesNotMatch(html,/data-kind="request"|data-kind="wait"/);
  assert.equal(timelineSummary(data).seconds,null);
});

test('synchronous calls with real endpoints use their action and actual interval without transport', () => {
  const data = {calls:[
    remote(1,undefined,0,10,{transport:undefined,stage:'seal',action:'整页单证识别',started_at:stamp(1),finished_at:stamp(6),seconds:100}),
    remote(2,undefined,0,10,{transport:undefined,stage:'routing',action:'印章专用场景识别',input_name:'dzt-seal-0-input.png',started_at:stamp(2),finished_at:stamp(7),seconds:200}),
  ]};
  const spans = resolveRunTimeline(data).runs[0].spans;
  assert.deepEqual(spans.map(item=>[item.lane,item.kind,item.seconds]),[
    ['danzhengtong_page','recognition',5],['danzhengtong_seal','recognition',5]]);
  assert.match(spans[1].label,/dzt-seal-0-input\.png/);
  assert.doesNotMatch(renderRunTimeline(data),/data-kind="request"|data-kind="wait"|100 秒|200 秒/);
});

test('a remote HTTP sum without a real end cannot fabricate any timeline interval', () => {
  const data = {processing_seconds:999,calls:[{id:1,method:'danzhengtong',action:'印章识别',
    started_at:stamp(0),seconds:999,status:'succeeded'}]};
  assert.deepEqual(resolveRunTimeline(data).runs,[]);
  assert.equal(timelineSummary(data).seconds,null);
  assert.doesNotMatch(renderRunTimeline(data),/999 秒|data-kind="recognition"/);
});

test('retained historical lanes never borrow a current call ID without matching provenance', () => {
  const old = {id:'old-run',kind:'retained',spans:[span('old-span','danzhengtong',0,10,{call_id:1})]};
  const data = {calls:[remote(1,'seal',0,10)],timeline:recorded([],{runs:[old]})};
  assert.equal(resolveRunTimeline(data).runs[0].spans[0].lane,'danzhengtong');
  data.calls[0] = remote(1,'page',0,10,{_run:{id:'old-run',retained:true}});
  assert.equal(resolveRunTimeline(data).runs[0].spans[0].lane,'danzhengtong_page');
});

test('expansion and selection are independent for the two remote lanes', () => {
  const data = {timeline:recorded([
    span('page','danzhengtong_page',0,10,{kind:'lifecycle'}),
    span('seal','danzhengtong_seal',1,12,{kind:'lifecycle'}),
  ])};
  const expanded = new Set([JSON.stringify(['current','danzhengtong_page'])]);
  const html = renderRunTimeline(data,{expandedLanes:expanded,selectedSpan:JSON.stringify(['current','seal'])});
  const lanes = [...html.matchAll(/<details class="run-timeline-lane"[^>]*>/g)].map(item=>item[0]);
  assert.equal(lanes.filter(item=>item.includes(' open')).length,1);
  assert.match(lanes[0],/danzhengtong_page/);
  assert.match(lanes[0],/ open/);
  assert.doesNotMatch(lanes[1],/ open/);
  assert.match(html,/class="run-timeline-selection"/);
});
