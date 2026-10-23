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

test('company expands into requirements, defaults to ungrouped and classifies only that company', async () => {
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
  assert(!$('#sample-grid').innerHTML.includes('grouped.jpg'));
  assert.equal($('#sample-requirement-title').textContent, '未归类');
  assert.match($('#sample-library-summary').textContent, /当前分组 1 张 · 本公司 2 张/);
  click({company:'甲公司',requirement:'甲章'});
  assert.match($('#sample-grid').innerHTML, /grouped.jpg/);
  assert(!$('#sample-grid').innerHTML.includes('pending.jpg'));
  assert(!$('#sample-grid').innerHTML.includes('other.jpg'));
  assert(!$('#sample-company-list').innerHTML.includes('全部签章要求'));
  assert.match($('#sample-company-list').innerHTML, /已建章 1 个/);
  click({company:'甲公司',requirement:''});
  await $('#sample-classify').handlers.click();
  assert.deepEqual(requests, ['甲公司']);
  assert.match(notices[0], /已归类 1 张/);
  assert.match($('#sample-grid').innerHTML, /当前条件下没有样单/);
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
  // The company action still checks all assigned samples even when its default ungrouped view is empty.
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
