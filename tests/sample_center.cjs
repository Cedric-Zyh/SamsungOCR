const test = require('node:test');
const assert = require('node:assert/strict');
const {sampleScope, sampleCard, sampleImageUrl} = require('../static/modules/samples/center.mjs');
const {createApiClients} = require('../static/modules/core/api.mjs');
const {ReceiptWorkbench} = require('../static/modules/core/workbench.mjs');
const {createSampleCenter} = require('../static/modules/samples/center.mjs');

test('company, filename and status filters compose without broadening a company', () => {
  const items = [
    {company:'甲公司', filename:'123.jpg', status:'未测试'},
    {company:'甲公司', filename:'456.jpg', status:'已完成'},
    {company:'乙公司', filename:'123.jpg', status:'未测试'},
  ];
  assert.deepEqual(sampleScope(items, {company:'甲公司', search:' 123 ', status:'未测试'}), [items[0]]);
  assert.deepEqual(sampleScope(items, {company:'不存在'}), []);
  assert.equal(sampleScope(items, {company:'甲公司'}).length, 2);
});

test('cards escape customer-controlled text and prevent running active tests', () => {
  const html = sampleCard({filename:'<img>.jpg', status:'测试中', job_status:'queued', error:'<script>x</script>'}, true);
  assert.match(html, /data-sample-run disabled/);
  assert.match(html, /data-sample-result disabled/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(sampleImageUrl('a #.jpg'), /a%20%23.jpg$/);
  assert.match(html, /data-sample-src="\/api\/samples\/image\//);
  assert.doesNotMatch(html, /\ssrc=/);
});

test('sample clients always use independent endpoints even for colliding result ids', async () => {
  const calls = [];
  const clients = createApiClients(async (...args) => calls.push(args));
  await clients.samples.get(1);
  await clients.samples.reviewHistory(1);
  await clients.samples.groundTruth(1);
  await clients.samples.run(['123.jpg']);
  await clients.samples.saveStandard(1, {candidate_id:'sample-seal',standard_revision:0});
  assert(calls.every(([url]) => url.startsWith('/api/samples/')));
  assert.deepEqual(calls[3][1].json, {filenames:['123.jpg']});
  assert.equal(calls[4][0], '/api/samples/results/1/seal-standard');
});

test('sample selected-seal previews never reference the production result id route', () => {
  const sources = ReceiptWorkbench.imageSources({id:1, sample_center:true,
    preview_url:'/files/samples/previews/test.jpg',
    seal_check:{api:{ok:true}, dual_check:{policy:'qingtong_any_channel', selected:{xyxy:[1,1,20,20]}}}});
  assert(sources.some(source => source.url === '/files/sample-selected-seal/1.png'));
  assert(!sources.some(source => source.url.startsWith('/files/selected-seal/')));
});

test('sample import uses multipart and delete stays scoped to the sample library', async () => {
  const calls = [];
  const clients = createApiClients(async (...args) => calls.push(args));
  const form = new FormData();
  form.append('company', '客户甲');
  await clients.samples.importFiles(form);
  await clients.samples.remove(['new.png']);
  assert.deepEqual(calls, [
    ['/api/samples/import', {method:'POST', body:form}],
    ['/api/samples/delete', {method:'POST', json:{filenames:['new.png']}}],
  ]);
});

test('unclassified stays second and auto classification refreshes groups without starting OCR', async () => {
  const nodes = new Map();
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'', dataset:{}, innerHTML:'', textContent:'', scrollTop:0, open:false,
      classList:{toggle() {}, add() {}, remove() {}}, handlers:{},
      addEventListener(name, handler) { this.handlers[name] = handler; },
      setAttribute() {}, contains() { return false; }, focus() {},
    });
    return nodes.get(selector);
  };
  let data = {items:[
    {filename:'7329228596.jpg',company:'未分类',status:'已完成',result_id:1},
    {filename:'other.jpg',company:'甲公司',status:'未测试'},
  ], companies:[{name:'甲公司',count:1},{name:'未分类',count:1}]};
  const notices = [];
  let classified = 0;
  const center = createSampleCenter({
    environment:{document:{activeElement:null},window:{addEventListener() {}}},
    ui:{$,$$:()=>[],toast:message=>notices.push(message)},
    services:{samples:{list:async()=>data, classify:async()=>{
      classified++;
      data = {items:data.items.map(row=>({...row,company:'甲公司'})),companies:[{name:'甲公司',count:2}]};
      return {classified:[{filename:'7329228596.jpg',company:'甲公司'}],created_companies:[],remaining:0};
    }}}, flushSettings:async()=>assert.fail('classification must reuse existing OCR'),
  });
  center.initialize();
  await center.load();
  const groups = () => [...$('#sample-company-list').innerHTML.matchAll(/data-company="([^"]*)"/g)].map(match=>match[1]);
  assert.deepEqual(groups(), ['', '未分类', '甲公司']);
  assert.equal($('#sample-classify').hidden, false);
  assert.equal($('#sample-classify').disabled, false);
  $('#sample-company-list').handlers.click({target:{closest:()=>({dataset:{company:'未分类'}})}});
  assert.equal($('#sample-classify').hidden, false);
  assert.equal($('#sample-classify').disabled, false);
  await $('#sample-classify').handlers.click();
  assert.equal(classified, 1);
  assert.deepEqual(groups(), ['', '未分类', '甲公司']);
  assert.equal($('#sample-company-title').textContent, '未分类');
  assert.equal($('#sample-classify').disabled, true);
  assert.match(notices[0], /已归类 1 张/);
});

test('company opens all its samples while explicit requirement clicks narrow the scope', async () => {
  const nodes = new Map();
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'', dataset:{}, innerHTML:'', textContent:'', scrollTop:0, open:false,
      classList:{toggle() {}, add() {}, remove() {}}, handlers:{},
      addEventListener(name, handler) { this.handlers[name] = handler; },
      setAttribute() {}, contains() { return false; }, focus() {},
    });
    return nodes.get(selector);
  };
  let data = {items:[
    {filename:'pending.jpg',company:'甲公司',requirement_key:'',status:'已完成',result_id:1},
    {filename:'grouped.jpg',company:'甲公司',requirement_key:'甲章',status:'已完成',result_id:2},
    {filename:'other.jpg',company:'乙公司',requirement_key:'甲章',status:'未测试'},
  ], companies:[{name:'甲公司',count:2},{name:'乙公司',count:1}],requirement_groups:{
    '甲公司':[{key:'',name:'未归类',count:1},{key:'甲章',name:'甲章',count:1,has_standard:true}],
  }};
  const notices = [], requests = [];
  const center = createSampleCenter({
    environment:{document:{activeElement:null},window:{addEventListener() {}}},
    ui:{$,$$:()=>[],toast:message=>notices.push(message)},
    services:{samples:{list:async()=>data, classifyRequirements:async company=>{
      requests.push(company);
      data = {...data,items:data.items.map(row=>row.filename === 'pending.jpg' ? {...row,requirement_key:'甲章'} : row),
        requirement_groups:{'甲公司':[{key:'甲章',name:'甲章',count:2,has_standard:true}]}};
      return {classified:[{}],created_groups:[],remaining:0};
    }}},flushSettings:async()=>assert.fail('must not rerun OCR'),
  });
  center.initialize(); await center.load();
  const click = dataset => $('#sample-company-list').handlers.click({target:{closest:()=>({dataset})}});
  click({company:'甲公司'});
  assert.match($('#sample-company-list').innerHTML, /aria-expanded="true"/);
  assert.match($('#sample-company-list').innerHTML, /✓ 已建章/);
  assert.match($('#sample-grid').innerHTML, /pending.jpg/);
  assert.match($('#sample-grid').innerHTML, /grouped.jpg/);
  assert(!$('#sample-grid').innerHTML.includes('other.jpg'));
  assert.equal($('#sample-requirement-title').textContent, '本公司全部样单');
  assert.match($('#sample-library-summary').textContent, /本公司 2 张/);
  assert.doesNotMatch($('#sample-library-summary').textContent, /当前分组/);
  click({company:'甲公司',requirement:'甲章'});
  assert.match($('#sample-grid').innerHTML, /grouped.jpg/);
  assert(!$('#sample-grid').innerHTML.includes('pending.jpg'));
  assert(!$('#sample-grid').innerHTML.includes('other.jpg'));
  assert(!$('#sample-company-list').innerHTML.includes('全部签章要求'));
  assert.match($('#sample-company-list').innerHTML, /已建章 1 个/);
  click({company:'甲公司',requirement:''});
  assert.match($('#sample-grid').innerHTML, /pending.jpg/);
  assert(!$('#sample-grid').innerHTML.includes('grouped.jpg'));
  assert.equal($('#sample-requirement-title').textContent, '未归类');
  await $('#sample-classify').handlers.click();
  assert.deepEqual(requests, ['甲公司']);
  assert.match(notices[0], /已归类 1 张/);
  assert.match($('#sample-grid').innerHTML, /当前条件下没有样单/);
  click({company:'甲公司'});
  assert.match($('#sample-grid').innerHTML, /pending.jpg/);
  assert.match($('#sample-grid').innerHTML, /grouped.jpg/);
  assert.equal($('#sample-requirement-title').textContent, '本公司全部样单');
  click({company:'甲公司',requirement:'甲章'});
  assert.match($('#sample-grid').innerHTML, /pending.jpg/);
  assert.match($('#sample-grid').innerHTML, /grouped.jpg/);
});

test('classification remains available for assigned samples and sends all, company, group or checked scope', async () => {
  const nodes = new Map();
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'',dataset:{},innerHTML:'',textContent:'',scrollTop:0,open:false,handlers:{},
      classList:{toggle() {},add() {},remove() {}},
      addEventListener(name, fn) { this.handlers[name] = fn; },
      setAttribute() {},contains() { return false; },focus() {},
    });
    return nodes.get(selector);
  };
  const data = {items:[
    {filename:'one.jpg',company:'甲公司',requirement_key:'章一',status:'已完成',result_id:1},
    {filename:'two.jpg',company:'甲公司',requirement_key:'章二',status:'已完成',result_id:2},
    {filename:'failed.jpg',company:'乙公司',status:'运行失败',result_id:3},
  ],companies:[{name:'甲公司',count:2},{name:'乙公司',count:1}],requirement_groups:{
    '甲公司':[{key:'章一',name:'章一',count:1},{key:'章二',name:'章二',count:1}],
  }};
  const calls = [];
  const response = {classified:[],created_companies:[],created_groups:[],processed:2,skipped:0};
  const center = createSampleCenter({
    environment:{document:{activeElement:null},window:{addEventListener() {}}},
    ui:{$,$$:()=>[],toast() {}},flushSettings:async()=>assert.fail('must not rerun OCR'),
    services:{samples:{list:async()=>data,
      classify:async scope=>{calls.push(scope);return response;},
      classifyRequirements:async(company,scope)=>{calls.push({company,...scope});return response;},
    }},
  });
  const click = dataset=>$('#sample-company-list').handlers.click({target:{closest:()=>({dataset})}});
  center.initialize(); await center.load();
  assert.equal($('#sample-classify').hidden,false);
  assert.equal($('#sample-classify').disabled,false);
  await $('#sample-classify').handlers.click();
  assert.deepEqual(calls.pop(),{company:'',requirement:null});
  click({company:'甲公司'});
  // Company selection and the company action both cover all assigned samples.
  assert.equal($('#sample-classify').disabled,false);
  await $('#sample-classify').handlers.click();
  assert.deepEqual(calls.pop(),{company:'甲公司',requirement:null});
  click({company:'甲公司',requirement:'章一'});
  await $('#sample-classify').handlers.click();
  assert.deepEqual(calls.pop(),{company:'甲公司',requirement:'章一'});
  $('#sample-grid').handlers.change({target:{matches:()=>true,checked:true,
    closest:()=>({dataset:{filename:'one.jpg'}})}});
  await $('#sample-classify').handlers.click();
  assert.deepEqual(calls.pop(),{company:'甲公司',requirement:null,filenames:['one.jpg']});
  click({company:'乙公司'});
  assert.equal($('#sample-classify').disabled,true);
});

function performanceHarness({list, run, history, openResult, images = false} = {}) {
  const nodes = new Map(), timers = new Map(), requests = [], windowEvents = new Map();
  let timestamp = 0, timerId = 0, pictures = [], gridWrites = 0;
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'', dataset:{}, innerHTML:'', textContent:'', scrollTop:0, open:false,
      classList:{toggle() {}, add() {}, remove() {}}, handlers:{},
      addEventListener(name, handler) { this.handlers[name] = handler; },
      setAttribute() {}, contains() { return false; }, focus() {}, close() { this.open = false; },
      showModal() { this.open = true; },
    });
    return nodes.get(selector);
  };
  if (images) {
    let markup = '';
    Object.defineProperty($('#sample-grid'), 'innerHTML', {
      get: () => markup,
      set(value) {
        gridWrites++;
        markup = value;
        pictures = [...value.matchAll(/data-sample-src="([^"]+)"/g)].map(match => ({dataset:{sampleSrc:match[1]}}));
      },
    });
  }
  const environment = {
    Date:{now:()=>timestamp}, AbortController, document:{activeElement:null},
    window:{addEventListener(name, callback) {
      if (!windowEvents.has(name)) windowEvents.set(name, []);
      windowEvents.get(name).push(callback);
    }, URL:{createObjectURL:()=>'', revokeObjectURL() {}}},
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(source, {signal}) { requests.push({source, signal}); return new Promise(()=>{}); },
  };
  const center = createSampleCenter({environment,
    ui:{$,$$:selector=>selector === '#sample-grid img[data-sample-src]' ? pictures : [],toast() {}},
    services:{samples:{list, run, history}}, flushSettings:async()=>{}, openResult,
  });
  center.initialize();
  return {center, $, timers, requests, at:value=>{timestamp=value;},
    runTimer:()=>{const [id, callback]=[...timers].at(-1);timers.delete(id);callback();},
    hashchange:()=>windowEvents.get('hashchange').forEach(callback=>callback()), writes:()=>gridWrites};
}

const performanceData = status => ({items:Array.from({length:18},(_,index)=>({
  filename:`sample-${index}.jpg`,company:'甲公司',status,
})),companies:[{name:'甲公司',count:18}]});

test('sample result and preview browsing follows the filtered library order across pages', async () => {
  const ids = [81, 12, 43, 25, 90, 34, 57, 16], opened = [];
  const matching = ids.map((result_id, index) => ({result_id,filename:`sample-${index}.jpg`,
    company:'甲公司',requirement_key:'甲章',status:'已完成'}));
  const data = {items:[...matching,
    {...matching[0],filename:'other-company.jpg',company:'乙公司',result_id:999},
    {...matching[0],filename:'sample-other-requirement.jpg',requirement_key:'乙章',result_id:998},
    {...matching[0],filename:'outside-search.jpg',result_id:997},
    {...matching[0],filename:'sample-untested.jpg',result_id:null,status:'未测试'},
  ],companies:[{name:'甲公司',count:11},{name:'乙公司',count:1}],
  requirement_groups:{'甲公司':[{key:'甲章',name:'甲章',count:10},{key:'乙章',name:'乙章',count:1}]}};
  const h = performanceHarness({list:async()=>data,openResult:async(...args)=>opened.push(args)});
  await h.center.load();
  h.$('#sample-company-list').handlers.click({target:{closest:()=>({dataset:{company:'甲公司',requirement:'甲章'}})}});
  h.$('#sample-search').value = 'sample-';
  h.$('#sample-search').handlers.input();
  h.$('#sample-next').handlers.click();
  assert.match(h.$('#sample-grid').innerHTML,/sample-6.jpg/);
  assert.doesNotMatch(h.$('#sample-grid').innerHTML,/sample-0.jpg/);
  const click = (filename, action) => h.$('#sample-grid').handlers.click({target:{closest:selector=>
    selector === '[data-filename]' ? {dataset:{filename}} : selector === action ? {} : null}});
  await click('sample-6.jpg','[data-sample-result]');
  assert.deepEqual(opened[0],[57,{browseIds:ids}]);
  await click('sample-7.jpg','[data-sample-preview]');
  assert.deepEqual(opened[1],[16,{preview:true,browseIds:ids}]);
  data.items.reverse();
  await h.center.load();
  assert.deepEqual(opened[0][1].browseIds,ids, 'a refreshed list must not reorder the open browsing session');
});

test('test history browsing preserves the displayed run order and historical result ids', async () => {
  const opened = [], history = [
    {filename:'same.jpg',result_id:38,status:'succeeded'},
    {filename:'pending.jpg',result_id:null,status:'queued'},
    {filename:'other.jpg',result_id:12,status:'succeeded'},
    {filename:'same.jpg',result_id:5,status:'succeeded'},
  ];
  const h = performanceHarness({list:async()=>performanceData('已完成'),history:async()=>history,
    openResult:async(...args)=>opened.push(args)});
  await h.center.load();
  await h.$('#sample-history-open').handlers.click();
  await h.$('#sample-history-body').handlers.click({target:{closest:()=>({dataset:{historyResult:'5'}})}});
  assert.deepEqual(opened,[[5,{browseIds:[38,12,5]}]]);
  assert.equal(h.$('#sample-history-dialog').open,false);
});

test('paging updates text immediately and an unchanged poll does not interrupt deferred thumbnails', async () => {
  const data = performanceData('未测试');
  const h = performanceHarness({list:async()=>data,images:true});
  await h.center.load();
  assert.equal(h.requests.length,2);
  h.$('#sample-next').handlers.click();
  assert.equal(h.$('#sample-page-number').textContent,'2 / 3');
  h.$('#sample-next').handlers.click();
  assert.equal(h.$('#sample-page-number').textContent,'3 / 3');
  assert(h.requests.every(request=>request.signal.aborted));
  assert.equal(h.requests.length,2);
  const [timer] = h.timers.keys();
  await h.center.load();
  assert.deepEqual([...h.timers.keys()],[timer]);
  h.runTimer();
  assert.deepEqual(h.requests.slice(2).map(request=>request.source),[
    '/api/samples/image/sample-12.jpg?thumbnail=1','/api/samples/image/sample-13.jpg?thumbnail=1',
  ]);
});

test('sample polling slows while idle, stays timely for active tests, and manual loads always refresh', async () => {
  let data = performanceData('未测试'), calls = 0;
  const h = performanceHarness({list:async()=>{calls++;return data;}});
  await h.center.load();
  h.at(2000); await h.center.poll();
  h.at(29999); await h.center.poll();
  assert.equal(calls,1);
  data = performanceData('测试中');
  h.at(30000); await h.center.poll();
  assert.equal(calls,2);
  h.at(31999); await h.center.poll();
  assert.equal(calls,2);
  data = performanceData('已完成');
  h.at(32000); await h.center.poll();
  assert.equal(calls,3);
  h.at(34000); await h.center.poll();
  assert.equal(calls,3);
  await h.center.load();
  assert.equal(calls,4);
  h.at(64000); await h.center.poll();
  assert.equal(calls,5);
});

test('returning to samples restores cancelled thumbnails without replacing the same page controls', async () => {
  const data = performanceData('未测试');
  const h = performanceHarness({list:async()=>data,images:true});
  await h.center.load();
  assert.equal(h.writes(),1);
  h.hashchange();
  assert(h.requests.every(request=>request.signal.aborted));
  await h.center.load();
  assert.equal(h.requests.length,4);
  assert.equal(h.writes(),1);
});

test('sample polling retries failures promptly even after an idle list was loaded', async () => {
  let calls = 0, fail = false;
  const h = performanceHarness({list:async()=>{calls++;if(fail)throw Error('Offline');return performanceData('未测试');}});
  await h.center.load();
  fail = true;
  h.at(30000); await h.center.poll();
  assert.match(h.$('#sample-feedback').textContent,/Offline/);
  h.at(31999); await h.center.poll();
  assert.equal(calls,2);
  fail = false;
  h.at(32000); await h.center.poll();
  assert.equal(calls,3);
  assert.equal(h.$('#sample-feedback').textContent,'');
});

test('starting tests refreshes past a list request that began before submission', async () => {
  let calls = 0, resolveOld;
  const oldList = new Promise(resolve=>{resolveOld=resolve;});
  const h = performanceHarness({list:async()=>{
    calls++;
    return calls === 2 ? oldList : performanceData(calls > 2 ? '测试中' : '未测试');
  },run:async names=>({accepted:names,skipped:[]})});
  await h.center.load();
  const stale = h.center.load();
  const running = h.center.run(['sample-0.jpg']);
  await new Promise(resolve=>setImmediate(resolve));
  resolveOld(performanceData('未测试'));
  await stale; await running;
  assert.equal(calls,3);
  assert.match(h.$('#sample-grid').innerHTML,/正在识别/);
  h.at(2000); await h.center.poll();
  assert.equal(calls,4);
});
