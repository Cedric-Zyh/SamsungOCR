const assert = require('node:assert/strict');
const test = require('node:test');
const {buildProcessHistory} = require('../static/modules/records/process_history_model.mjs');
const {renderProcessHistory, executionLabel} = require('../static/modules/records/process_history_view.mjs');
const {createProcessHistory} = require('../static/modules/records/process_history.mjs');

const time = second => `2026-10-20T10:52:${String(second).padStart(2,'0')}+08:00`;
const lifecycle = (kind, second) => ({kind, time:time(second), title:kind, status:kind, metadata:{}});
const progress = (stage, method, phase, second, extra={}) => ({kind:'progress', time:time(second), title:`${stage} · ${phase}`,
  metadata:{provider:'model', target:stage, stage, method, phase, ...extra}});

test('pairs parallel models independently and preserves repeated calls and zero durations', () => {
  const model=buildProcessHistory({job:{status:'succeeded',started_at:time(24),finished_at:time(38),processing_seconds:14},events:[
    lifecycle('running',24), progress('seal','qingtong','started',32,{channel:'parallel'}),
    progress('seal','paddle_v6','started',33), progress('seal','qingtong','completed',34,{channel:'parallel',elapsed_seconds:1.7}),
    progress('seal','paddle_v6','completed',37,{elapsed_seconds:4.13}),
    progress('seal','qingtong','started',37), progress('seal','qingtong','completed',37,{elapsed_seconds:0}),
    progress('seal','qingtong','started',37), progress('seal','qingtong','completed',38,{elapsed_seconds:.5}), lifecycle('succeeded',38),
  ]});
  assert.equal(model.attempts[0].steps.length,4);
  assert.deepEqual(model.attempts[0].steps.map(s=>s.duration),[1.7,4.13,0,.5]);
  assert(model.attempts[0].steps.every(s=>s.events.length===2));
  assert.equal(model.processing,14); // wall time, never sum concurrent steps
  assert.equal(model.parallel,true);
});

test('success reflects processing, not a pending review verdict, including the legacy log', () => {
  const model=buildProcessHistory({current_status:'需人工复核',events:[
    {...lifecycle('succeeded',38),detail:'需人工复核'},
    {kind:'review',title:'人工复核',detail:'待复核',time:time(40)},
  ]});
  assert.equal(model.status,'succeeded');
  assert.equal(executionLabel(model.status),'处理完成');
  assert.doesNotMatch(renderProcessHistory(model),/人工复核|待复核/);
  assert.equal(model.processing,null);
  assert.equal(model.queueStatus,'unknown');
});

test('retries retain separate runs and a queued retry does not overwrite past timing', () => {
  const events=[lifecycle('running',24),progress('seal','qingtong','started',24),progress('seal','qingtong','failed',28),lifecycle('failed',28),
    lifecycle('retry_queued',30),lifecycle('running',32),progress('seal','qingtong','started',32),progress('seal','qingtong','completed',36),lifecycle('succeeded',38)];
  const completed=buildProcessHistory({events});
  assert.equal(completed.attempts.length,2);
  assert.deepEqual(completed.attempts.map(a=>a.status),['failed','succeeded']);
  const queued=buildProcessHistory({events:[...events,lifecycle('retry_queued',40)],job:{status:'queued',started_at:'',processing_seconds:0}});
  assert.equal(queued.status,'queued');
  assert.equal(queued.processing,6);
  assert.equal(queued.recognitionStatus,'pending');
});

test('a failed or cancelled run never marks uncompleted steps successful', () => {
  for(const status of ['failed','cancelled']) {
    const model=buildProcessHistory({events:[lifecycle('running',24),progress('seal','qingtong','started',24),lifecycle(status,30)]});
    assert.equal(model.status,status);
    assert.equal(model.attempts[0].steps[0].status,status==='cancelled'?'cancelled':'incomplete');
    assert.equal(model.attempts[0].steps[0].end,'');
  }
});

test('preparing closes at the next model stage, planned remaining work stays pending', () => {
  const model=buildProcessHistory({job:{status:'running',started_at:time(24),progress:{steps:[{key:'date:paddle_v6',target_id:'date',method:'paddle_v6',phase:'pending'}]}},events:[
    lifecycle('running',24),progress('preparing','system','started',24),progress('routing','paddle_v6','started',25)]});
  assert.deepEqual(model.attempts[0].steps.map(s=>s.status),['succeeded','running','pending']);
  assert.equal(model.attempts[0].steps[0].end,time(25));
});

test('missing timestamps are not replaced by fabricated elapsed times; dynamic text is escaped', () => {
  const model=buildProcessHistory({events:[progress('<img src=x onerror=alert(1)>','<script>bad</script>','completed',28)]});
  assert.equal(model.attempts[0].steps[0].duration,null);
  const html=renderProcessHistory(model);
  assert.doesNotMatch(html,/<script>|<img src=x/);
  assert.match(html,/&lt;img/);
});

function harness() {
  const nodes=new Map(),requests=[];
  const $=key=>{
    if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:'',dataset:{},setAttribute(){},
      classList:{add(){},remove(){}},open:false,showModal(){this.open=true;},close(){this.open=false;}});
    return nodes.get(key);
  };
  const history=createProcessHistory({environment:{document:{body:{classList:{add(){},remove(){}}}},URLSearchParams},ui:{$,toast(){}},
    services:{records:{processHistory:()=>new Promise(resolve=>requests.push(resolve))}}});
  return {$,history,requests};
}

test('late requests cannot overwrite a newer file or populate a closed dialog', async () => {
  const h=harness();
  const first=h.history.open({resultId:1}),second=h.history.open({resultId:2});
  h.requests[1]({filename:'new.jpg',events:[]});await second;
  h.requests[0]({filename:'old.jpg',events:[]});await first;
  assert.equal(h.$('#process-history-title').textContent,'new.jpg');
  const pending=h.history.open({resultId:3});h.history.close();
  h.requests[2]({filename:'closed.jpg',events:[]});await pending;
  assert.notEqual(h.$('#process-history-title').textContent,'closed.jpg');
});
