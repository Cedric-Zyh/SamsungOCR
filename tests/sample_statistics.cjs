const test = require('node:test');
const assert = require('node:assert/strict');
const {summarizeCells, filterStatistics, statisticsCell, statisticsDistribution} = require('../static/modules/samples/statistics.mjs');
const {createApiClients} = require('../static/modules/core/api.mjs');
const {createSampleStatistics} = require('../static/modules/samples/statistics.mjs');

const cell = (state, decision = '') => ({state, decision, value:'章'});
const row = (company, template, ocr, local) => ({company, date:cell('正确'),
  seals:{qingtong_template:template,qingtong_ocr:ocr,'local:paddle_v6':local}});

test('accuracy includes errors, missing and failure; excludes pending, absent and unlabeled', () => {
  const result = summarizeCells(['正确','正确','错误','未识别','失败','未运行','未测试','测试中','待标注'].map(state => cell(state)));
  assert.equal(result.total, 5);
  assert.equal(result.accuracy, .4);
  assert.equal(summarizeCells([cell('未运行')]).accuracy, null);
});

test('disagreement compares decisions instead of correctness or unavailable channels', () => {
  const rows = [
    row('甲',cell('正确','匹配'),cell('错误','不匹配'),cell('正确','匹配')),
    row('乙',cell('正确','匹配'),cell('未运行'),cell('失败')),
    row('甲',cell('正确','匹配'),cell('正确','匹配'),cell('错误','不匹配')),
    row('甲',cell('正确','不匹配'),cell('正确','不匹配'),cell('未运行')),
  ];
  assert.deepEqual(filterStatistics(rows,{filter:'remote'}), [rows[0]]);
  assert.deepEqual(filterStatistics(rows,{filter:'local'}), [rows[0],rows[2]]);
  assert.deepEqual(filterStatistics(rows,{filter:'errors',company:'甲'}), [rows[0],rows[2]]);
  assert.deepEqual(filterStatistics(rows,{filter:'missing'}), [rows[1]]);
  assert.deepEqual(filterStatistics(rows,{tab:'date',filter:'errors'}), []);
});

test('cell text and error details are escaped', () => {
  const markup = statisticsCell({state:'错误',value:'<img onerror=x>',decision:'<script>',note:'<svg/onload=x>'});
  assert(!markup.includes('<img'));
  assert(markup.includes('&lt;img'));
  assert(markup.includes('&lt;svg'));
  assert(!statisticsCell({state:'错误',value:'2025-01-01',decision:'匹配'}, false).includes('判定'));
});

test('batch scope uses sample endpoint and URL encoding', async () => {
  const urls = [];
  const clients = createApiClients(async url => urls.push(url));
  await clients.samples.statistics();
  await clients.samples.statistics('batch & 2');
  assert.deepEqual(urls, ['/api/samples/statistics','/api/samples/statistics?task_id=batch+%26+2']);
});

test('Excel export uses current scope, downloads XLSX and recovers from failure', async () => {
  const nodes = new Map(), urls = [], downloads = [], notices = [];
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'', innerHTML:'', textContent:'', open:false, handlers:{}, dataset:{},
      classList:{toggle() {}}, setAttribute() {},
      addEventListener(type, fn) { this.handlers[type] = fn; },
      showModal() { this.open = true; },
    });
    return nodes.get(selector);
  };
  const dateTab = $('[date-tab]'); dateTab.dataset.statTab = 'date';
  let failed = false;
  const environment = {
    document:{activeElement:null, body:{appendChild() {}},createElement:()=>({
      click() { downloads.push(this.download); },remove() {},
    })},
    window:{addEventListener() {},setTimeout(fn) { fn(); },
      URL:{createObjectURL:()=> 'blob:test',revokeObjectURL() {}},
      fetch:async url=>{ urls.push(url); return failed
        ? {ok:false,json:async()=>({error:'导出失败示例'})}
        : {ok:true,blob:async()=>new Blob(['xlsx'])}; },
    },
  };
  const services = createApiClients(async()=>({items:[],columns:[],batches:[]}));
  const stats = createSampleStatistics({environment,ui:{$,$$:selector=>selector === '[data-stat-tab]' ? [dateTab] : [],
    toast:message=>notices.push(message)},services});
  stats.initialize();
  await stats.show({company:'客户 & 甲',companies:[]});
  dateTab.handlers.click();
  await $('#sample-statistics-export').handlers.click();
  const url = new URL(urls[0], 'http://localhost');
  assert.equal(url.pathname, '/api/samples/statistics/export');
  assert.equal(url.searchParams.get('company'), '客户 & 甲');
  assert.equal(url.searchParams.get('tab'), 'date');
  assert.equal(downloads[0], '样单统计-客户 & 甲-日期结果.xlsx');
  assert.equal($('#sample-statistics-export').disabled, false);
  failed = true;
  await $('#sample-statistics-export').handlers.click();
  assert.equal(notices.at(-1), '导出失败示例');
  assert.equal($('#sample-statistics-export').textContent, '导出 Excel');
  assert.equal($('#sample-statistics-export').disabled, false);
});

function statisticsHarness(items) {
  const nodes = new Map(), exports = [], requests = [], environment = {
    document:{activeElement:null,body:{appendChild() {}},createElement:()=>({click() {},remove() {}})},
    window:{addEventListener() {},setTimeout(fn) { fn(); },URL:{createObjectURL:()=> 'blob:test',revokeObjectURL() {}},
      fetch:async url=>{exports.push(url);return {ok:true,blob:async()=>new Blob(['xlsx'])};}},
  };
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'',innerHTML:'',textContent:'',open:false,handlers:{},dataset:{},attrs:{},
      classList:{toggle() {}},setAttribute(key,value) { this.attrs[key] = value; },
      addEventListener(type,fn) { this.handlers[type] = fn; },showModal() { this.open = true; },
      focus() { environment.document.activeElement = this; },
    });
    return nodes.get(selector);
  };
  const sections = ['overview','details'].map(section=>{
    const node = $(`#sample-statistics-${section}-tab`); node.dataset.statSection = section; return node;
  });
  const types = ['date','seal'].map(type=>{const node = $(`[type-${type}]`);node.dataset.statTab=type;return node;});
  const $$ = selector => selector === '[data-stat-section]' ? sections : selector === '[data-stat-tab]' ? types : [];
  const data = {items,columns:[{key:'qingtong_template',label:'清瞳 · 印章模板'}],
    batches:[{id:'batch1',created_at:'2026-10-23T10:00:00',total:items.length}]};
  const controller = createSampleStatistics({environment,ui:{$,$$,toast() {}},services:{samples:{
    statistics:async taskId=>{requests.push(taskId);return data;},
    statisticsExportUrl:params=>'http://localhost/export?' + new URLSearchParams(params),
  }}});
  controller.initialize();
  return {$,sections,types,controller,exports,requests,environment};
}

test('overview and detail views stay separate while preserving filters, pagination and batch', async () => {
  const items = Array.from({length:13},(_,index)=>({
    ...row(index === 12 ? '乙公司' : '甲公司',cell(index % 2 ? '错误' : '正确','匹配'),cell('正确'),cell('正确')),
    filename:`sample${index}.jpg`,job_status:'succeeded',expected_seal:'匹配',requirement:'客户甲公章',
  }));
  const {$,sections,types,controller,exports,requests} = statisticsHarness(items);
  await controller.show({company:'甲公司',companies:[{name:'甲公司'},{name:'乙公司'}]});
  assert.match($('#sample-statistics-body').innerHTML,/sample-stat-cards/);
  assert(!$('#sample-statistics-body').innerHTML.includes('<table'));
  assert.equal($('#sample-statistics-pagination').hidden,true);
  sections[1].handlers.click();
  assert.match($('#sample-statistics-body').innerHTML,/<table/);
  assert(!$('#sample-statistics-body').innerHTML.includes('sample-stat-cards'));
  assert.equal($('#sample-statistics-note').hidden,true);
  $('#sample-statistics-body').handlers.click({target:{closest:selector=>selector === '[data-stat-filter]'
    ? {dataset:{statFilter:'errors'}} : null}});
  $('#sample-statistics-next').handlers.click();
  assert.match($('#sample-statistics-body').innerHTML,/sample11.jpg/);
  assert.match($('#sample-statistics-page').textContent,/2 \/ 2/);
  sections[0].handlers.click();
  assert.match($('#sample-statistics-body').innerHTML,/50.0%/); // Overview uses the whole company.
  assert(!$('#sample-statistics-body').innerHTML.includes('data-stat-filter'));
  await $('#sample-statistics-export').handlers.click();
  assert.equal(new URL(exports.at(-1)).searchParams.get('filter'),'');
  sections[1].handlers.click();
  assert.match($('#sample-statistics-page').textContent,/2 \/ 2/);
  assert.match($('#sample-statistics-body').innerHTML,/data-stat-filter="errors"[^>]*aria-pressed="true"/);
  await $('#sample-statistics-export').handlers.click();
  assert.equal(new URL(exports.at(-1)).searchParams.get('filter'),'errors');
  assert.equal(new URL(exports.at(-1)).searchParams.get('company'),'甲公司');
  types[0].handlers.click();
  assert(!$('#sample-statistics-body').innerHTML.includes('清瞳 · 印章模板'));
  types[1].handlers.click();
  assert.match($('#sample-statistics-body').innerHTML,/data-stat-filter="errors"[^>]*aria-pressed="true"/);
  $('#sample-statistics-batch').handlers.change({target:{value:'batch1'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.at(-1),'batch1');
  sections[0].handlers.click(); sections[1].handlers.click();
  await $('#sample-statistics-export').handlers.click();
  assert.equal(new URL(exports.at(-1)).searchParams.get('task_id'),'batch1');
});

test('statistics tabs support keyboard selection and distribution excludes unscored results', async () => {
  const {sections,controller,environment,$} = statisticsHarness([]);
  await controller.show({company:'',companies:[]});
  let prevented = false;
  sections[0].handlers.keydown({key:'ArrowRight',preventDefault() { prevented=true; }});
  assert(prevented);
  assert.equal(sections[1].attrs['aria-selected'],'true');
  assert.equal(sections[0].attrs.tabindex,'-1');
  assert.equal(environment.document.activeElement,sections[1]);
  sections[1].handlers.keydown({key:'Home',preventDefault() {}});
  assert.equal(sections[0].attrs['aria-selected'],'true');
  assert.equal($('#sample-statistics-body').attrs['aria-labelledby'],'sample-statistics-overview-tab');
  const distribution = statisticsDistribution(summarizeCells(['正确','错误','未识别','失败','未测试'].map(state=>cell(state))));
  assert.equal((distribution.match(/width:25%/g)||[]).length,4);
  assert(!distribution.includes('未测试'));
  assert(!statisticsDistribution(summarizeCells([])).includes('NaN'));
});
