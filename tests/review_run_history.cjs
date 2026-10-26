const assert = require('node:assert/strict');
const test = require('node:test');
const {execFileSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const {renderRunHistory, runDuration, callImageUrl} = require('../static/modules/review/run_history_view.mjs');
const {createReviewRunHistory} = require('../static/modules/review/run_history.mjs');

const time = second => `2026-10-26T14:32:${String(second).padStart(2,'0')}+08:00`;
const call = (id, extra = {}) => ({id,stage:'seal',method:'paddle_v6',method_label:'Paddle OCR',
  action:'印章文字识别',input_name:`seal_${id}.png`,width:640,height:180,seconds:2,
  status:'succeeded',started_at:time(id),...extra});
const response = (calls = [call(1),call(2),call(3,{seconds:3,status:'failed',error:'识别超时'})], extra = {}) => ({
  filename:'customer/receipt.jpg',available:true,status:'succeeded',
  summary:{total:3,succeeded:2,failed:1,cache_hits:4},calls,
  started_at:time(0),finished_at:time(5),processing_seconds:5,result_id:9,...extra});

test('UTC start times display in the browser local timezone and invalid times stay unknown', () => {
  const moduleUrl = pathToFileURL(require.resolve('../static/modules/review/run_history_view.mjs')).href;
  const result = execFileSync(process.execPath, ['--input-type=module', '-e',
    `import {runTime} from ${JSON.stringify(moduleUrl)}; process.stdout.write(JSON.stringify([runTime('2026-10-26T01:20:30Z'),runTime('2026-10-26T01:20:30+00:00'),runTime('invalid'),runTime('')]));`],
    {env:{...process.env,TZ:'Asia/Shanghai'},encoding:'utf8'});
  assert.deepEqual(JSON.parse(result),['2026-10-26 09:20:30','2026-10-26 09:20:30','未记录','未记录']);
  assert.match(renderRunHistory(response([],{status:'completed'})),/已完成/);
});

test('latest input calls stay distinct, including repeated inputs, and parallel sums are not wall time', () => {
  const html = renderRunHistory(response([call(1,{input_name:'seal_crop.png'}),call(2,{input_name:'seal_crop.png'}),call(3,{seconds:3})]));
  assert.equal((html.match(/class="review-call-input">seal_crop.png/g)||[]).length,2);
  assert.match(html,/实际调用<\/span><strong>3<\/strong>/);
  assert.match(html,/复用已有识别结果 4 次，未计入实际调用/);
  assert.match(html,/处理用时 <strong>5 秒/);
  assert.match(html,/title="调用耗时合计">7 秒/);
  assert.match(html,/并行识别时，各次耗时相加可能超过总处理用时/);
  assert.doesNotMatch(html,/data-runs-result|data-runs-more|第 \d+ 次运行|历史测试/);
  assert.match(html,/data-call-explanation="count">/);
  assert.doesNotMatch(html,/data-call-explanation="count" open|receipt.jpg/);
});

test('seal inputs expand by default, errors and names escape markup, and images remain in the panel', () => {
  const html = renderRunHistory(response([
    call(1,{stage:'routing',input_name:'whole.jpg'}),
    call(2,{input_name:'folder/<script>x</script>.png',method_label:'<b>OCR</b>',status:'failed',error:'<img src=x onerror=alert(1)>',image_url:'/files/samples/artifacts/a/seal.png'}),
  ]));
  assert.match(html,/data-call-group="seal" open/);
  assert.doesNotMatch(html,/data-call-group="routing" open/);
  assert.match(html,/data-call-image="2"/);
  assert.match(html,/src="\/files\/samples\/artifacts\/a\/seal.png"/);
  assert.match(html,/&lt;b&gt;OCR&lt;\/b&gt;/);
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html,/<script>|<img src=x|target="_blank"/);
  assert.match(html,/1 次失败/);
  assert.match(html,/640 × 180/);
});

test('preview URLs permit only existing local artifacts and reject traversal or executable URLs', () => {
  for (const url of ['/files/artifacts/abc/seal.png','/files/samples/artifacts/a/seal.png','/files/previews/a/page.jpg','/files/artifacts/run/印章%201.png']) {
    assert.equal(callImageUrl(url),url);
  }
  for (const url of ['javascript:alert(1)','https://example.com/a.png','//example.com/a.png','data:image/png;base64,AAAA',
    '/files/artifacts/../secret.png','/files/artifacts/%2e%2e/secret.png','/files/artifacts/a%5cb.png','/files/artifacts/%00x.png',
    '/files/artifacts/%bad.png','/files/artifacts/raw space.png','/files/artifacts/%09x.png','/files/artifacts/%7fx.png','/files/customer/file.jpg']) assert.equal(callImageUrl(url),'');
  assert.doesNotMatch(renderRunHistory(response([call(1,{image_url:'https://example.com/pixel'})])),/data-call-image/);
});

test('missing historical measurements show dashes, while explicitly recorded zero stays zero', () => {
  const old = renderRunHistory(response([],{available:false,summary:{total:12,succeeded:12},note:'旧记录未保存调用明细，请重新测试。'}));
  assert.equal((old.match(/<strong>—<\/strong>/g)||[]).length,3);
  assert.match(old,/旧记录未保存调用明细，请重新测试/);
  assert.doesNotMatch(old,/<strong>12<\/strong>|处理用时/);
  const interrupted = renderRunHistory(response([],{available:false,status:'interrupted',summary:null,
    note:'最近一次识别已中断，未保存完整调用明细。'}));
  assert.match(interrupted,/data-state="interrupted">处理中断/);
  assert.equal((interrupted.match(/<strong>—<\/strong>/g)||[]).length,3);
  assert.match(interrupted,/最近一次识别已中断/);
  const empty = renderRunHistory(response([],{summary:{total:0,succeeded:0,failed:0,cache_hits:1},processing_seconds:0}));
  assert.match(empty,/实际调用<\/span><strong>0<\/strong>/);
  assert.match(empty,/没有发生实际识别调用/);
  assert.equal(runDuration(null),'未记录'); assert.equal(runDuration(undefined),'未记录');
  assert.equal(runDuration(''),'未记录'); assert.equal(runDuration(false),'未记录');
  assert.equal(runDuration(0),'0 秒'); assert.equal(runDuration(-1),'未记录');
  assert.equal(runDuration(.001),'< 0.01 秒');
  assert.match(renderRunHistory(response([call(1,{seconds:null}),call(2)])),/title="调用耗时合计">未记录/);
});

function harness() {
  const nodes = new Map(), calls = [], environment = {}, document = makeNode();
  environment.document = document;
  function makeNode() {
    const handlers = {}, attributes = {}, classes = new Set();
    let html = '', details = [];
    return {hidden:false,inert:false,disabled:false,isConnected:true,textContent:'',dataset:{},scrollTop:0,
      handlers,attributes,classes,
      classList:{add:key=>classes.add(key),remove:key=>classes.delete(key)},
      addEventListener(type,fn) { (handlers[type] ||= []).push(fn); },
      async emit(type,event = {}) { for (const fn of handlers[type] || []) await fn(event); },
      setAttribute(key,value) { attributes[key] = value; }, getAttribute:key=>attributes[key],
      focus() { document.activeElement = this; },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value;
        details = [...html.matchAll(/<details[^>]*(data-call-group|data-call-image|data-call-explanation)="([^"]*)"([^>]*)>/g)]
          .map(match=>({open:match[3].includes(' open'),attr:match[1],key:match[2],getAttribute(){return this.key;}}));
      },
      querySelectorAll(selector) { return details.filter(node=>`[${node.attr}]` === selector); },
    };
  }
  const $ = selector => nodes.get(selector) || null;
  for (const selector of ['#review-runs-open','[data-run-count]','#review-dialog']) nodes.set(selector,makeNode());
  const services = Object.fromEntries(['samples','records'].map(source=>[source,{recognitionCalls:id=>
    new Promise((resolve,reject)=>calls.push({source,id,resolve,reject}))}]));
  const history = createReviewRunHistory({environment,ui:{$},services});
  history.initialize();
  function mount(id = 9,sample = true) {
    for (const selector of ['#review-runs-panel','#review-runs-body','#review-runs-title','.review-layout','#review-form','[data-runs-close]']) nodes.set(selector,makeNode());
    $('#review-dialog').open = true;
    $('#review-runs-panel').hidden = true;
    $('#review-form').editedValue = '尚未保存的标注';
    history.setContext({id,filename:`${id}.jpg`},{sample});
  }
  function target(selector) { const node = makeNode(); node.closest = key=>key === selector ? node : null; return node; }
  const click = selector => $('#review-runs-panel').emit('click',{target:target(selector)});
  mount();
  return {$,history,calls,document,mount,click};
}

test('panel loads lazily and closing restores the exact form, scroll, and focus', async () => {
  const h = harness(), form = h.$('#review-form');
  form.scrollTop = 735; form.scrollLeft = 28;
  assert.equal(h.calls.length,0);
  const opening = h.history.open();
  assert.equal(form.hidden,true); assert.equal(form.inert,true);
  assert.equal(h.history.isOpen(),true);
  h.calls[0].resolve(response()); await opening;
  form.scrollTop = 0; form.scrollLeft = 0;
  assert.equal(h.$('[data-run-count]').textContent,'3 次');
  assert.equal(h.$('#review-runs-open').attributes['aria-expanded'],'true');
  await h.click('[data-runs-close]');
  assert.equal(h.history.isOpen(),false);
  assert.equal(form.hidden,false); assert.equal(form.inert,false);
  assert.equal(form.scrollTop,735); assert.equal(form.scrollLeft,28);
  assert.equal(form.editedValue,'尚未保存的标注');
  assert.equal(h.document.activeElement,h.$('#review-runs-open'));
  await h.history.open();
  assert.equal(h.calls.length,1);
});

test('late results cannot overwrite a closed panel or a different receipt and sources stay isolated', async () => {
  const h = harness();
  const old = h.history.open(); h.history.close();
  h.calls[0].resolve(response()); await old;
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML,/Paddle/);
  const next = h.history.open(); h.history.clear(); h.mount(10,false);
  const newest = h.history.open();
  assert.deepEqual(h.calls.map(call=>[call.source,call.id]),[['samples',9],['samples',9],['records',10]]);
  h.calls[2].resolve(response([call(1,{input_name:'new.jpg'})],{filename:'new.jpg'})); await newest;
  h.calls[1].resolve(response([call(1,{input_name:'old.jpg'})],{filename:'old.jpg'})); await next;
  assert.match(h.$('#review-runs-body').innerHTML,/new.jpg/);
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML,/old.jpg/);
});

test('Escape and dialog cancellation consume only the side panel and preserve form edits', async () => {
  const h = harness(), opening = h.history.open(); h.calls[0].resolve(response()); await opening;
  const events = [];
  const event = {key:'Escape',preventDefault(){events.push('prevent');},stopPropagation(){events.push('stop');},stopImmediatePropagation(){events.push('immediate');}};
  await h.document.emit('keydown',event);
  assert.equal(h.history.isOpen(),false); assert.equal(h.$('#review-form').editedValue,'尚未保存的标注');
  assert.deepEqual(events,['prevent','immediate','stop']);
  await h.document.emit('keydown',event); assert.equal(events.length,3);
  await h.history.open(); await h.$('#review-dialog').emit('cancel',event);
  assert.equal(h.history.isOpen(),false); assert.equal(h.$('#review-dialog').open,true);
});

test('refresh replaces the latest calls while preserving expanded groups, image previews and scroll', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].resolve(response([call(1,{image_url:'/files/artifacts/x/seal.png'}),call(2,{stage:'date'})])); await opening;
  const body = h.$('#review-runs-body');
  body.scrollTop = 410;
  body.querySelectorAll('[data-call-group]').find(row=>row.key==='date').open = true;
  body.querySelectorAll('[data-call-image]')[0].open = true;
  body.querySelectorAll('[data-call-explanation]')[0].open = true;
  const refreshing = h.click('[data-runs-refresh]');
  assert.match(body.innerHTML,/data-runs-refresh disabled/);
  await h.click('[data-runs-refresh]'); assert.equal(h.calls.length,2);
  h.calls[1].resolve(response([call(1,{input_name:'latest.png',image_url:'/files/artifacts/x/seal.png'}),call(2,{stage:'date'})])); await refreshing;
  assert.match(body.innerHTML,/latest.png/); assert.doesNotMatch(body.innerHTML,/seal_1.png/);
  assert.match(body.innerHTML,/data-call-group="date" open/);
  assert.match(body.innerHTML,/data-call-image="1" open/);
  assert.match(body.innerHTML,/data-call-explanation="count" open/);
  assert.equal(body.scrollTop,410);
});

test('refreshing an unrecorded in-progress snapshot opens the first available seal group', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].resolve(response([],{available:false,status:'running',summary:null,
    note:'本次正在识别，完成后可查看实际调用次数和耗时。'})); await opening;
  assert.equal(h.$('[data-run-count]').textContent,'—');
  const refresh = h.click('[data-runs-refresh]'); h.calls[1].resolve(response()); await refresh;
  const body = h.$('#review-runs-body');
  assert.match(body.innerHTML,/data-call-group="seal" open/);
  body.querySelectorAll('[data-call-group]')[0].open = false;
  const again = h.click('[data-runs-refresh]'); h.calls[2].resolve(response()); await again;
  assert.doesNotMatch(body.innerHTML,/data-call-group="seal" open/);
});

test('refresh errors retain existing data and first-load failures escape errors and allow retry', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].reject(Error('<broken>')); await opening;
  assert.match(h.$('#review-runs-body').innerHTML,/&lt;broken&gt;/);
  assert.match(h.$('#review-runs-body').innerHTML,/data-runs-refresh/);
  const retry = h.click('[data-runs-refresh]'); h.calls[1].resolve(response()); await retry;
  assert.match(h.$('#review-runs-body').innerHTML,/seal_1.png/);
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML,/broken/);
  const refresh = h.click('[data-runs-refresh]'); h.calls[2].reject(Error('暂时断开')); await refresh;
  assert.match(h.$('#review-runs-body').innerHTML,/暂时断开/);
  assert.match(h.$('#review-runs-body').innerHTML,/seal_1.png/);
});

test('a delayed old close event cannot clear a reopened dialog and dynamic panels bind once', async () => {
  const h = harness();
  h.history.setContext({id:9},{sample:true});
  assert.equal(h.$('#review-runs-panel').handlers.click.length,1);
  await h.$('#review-dialog').emit('close');
  assert.equal(h.$('#review-runs-open').hidden,false);
  const opening = h.history.open(); h.calls[0].resolve(response([],{available:false})); await opening;
  assert.equal(h.$('[data-run-count]').textContent,'—');
  h.$('#review-dialog').open = false; await h.$('#review-dialog').emit('close');
  assert.equal(h.history.isOpen(),false); assert.equal(h.$('#review-runs-open').hidden,true);
  h.mount(10); assert.equal(h.$('#review-runs-panel').handlers.click.length,1);
});
