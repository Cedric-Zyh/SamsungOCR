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

function statisticsHarness(items, {dataForTask} = {}) {
  const nodes = new Map(), exports = [], requests = [], opened = [], environment = {
    document:{activeElement:null,body:{appendChild() {}},createElement:()=>({click() {},remove() {}})},
    window:{addEventListener() {},setTimeout(fn) { fn(); },URL:{createObjectURL:()=> 'blob:test',revokeObjectURL() {}},
      fetch:async url=>{exports.push(url);return {ok:true,blob:async()=>new Blob(['xlsx'])};}},
  };
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'',innerHTML:'',textContent:'',open:false,handlers:{},dataset:{},attrs:{},
      classList:{toggle() {}},setAttribute(key,value) { this.attrs[key] = value; },
      addEventListener(type,fn) { this.handlers[type] = fn; },showModal() { this.open = true; },
      close() { this.open = false; },
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
    statistics:async taskId=>{requests.push(taskId);return dataForTask ? dataForTask(taskId, data) : data;},
    statisticsExportUrl:params=>'http://localhost/export?' + new URLSearchParams(params),
  }},openResult:async(...args)=>{ opened.push(args); $('#review-dialog').open = true; }});
  controller.initialize();
  return {$,sections,types,controller,exports,requests,opened,environment};
}

test('statistics browsing keeps the filtered row order across pages and selected batch results', async () => {
  const ids = [90,21,700,45,null,18,12,6];
  const matching = ids.map((result_id,index)=>({
    ...row('甲公司',cell('错误','匹配'),cell('正确','不匹配'),cell('未运行')),
    filename:`sample${index}.jpg`,result_id,job_status:'succeeded',expected_seal:'不匹配',requirement:'甲公章',
  }));
  const items = [...matching,
    {...matching[0],filename:'company.jpg',company:'乙公司',result_id:991},
    {...matching[0],filename:'requirement.jpg',requirement:'乙公章',result_id:992},
    {...matching[0],filename:'correct-channel.jpg',result_id:993,
      seals:{qingtong_template:cell('正确','不匹配'),qingtong_ocr:cell('错误','匹配')}},
  ];
  const {$,controller,opened} = statisticsHarness(items,{dataForTask:(taskId,data)=>taskId
    ? {...data,items:data.items.map(item=>({...item,result_id:item.result_id ? item.result_id + 1000 : null}))} : data});
  await controller.show({company:'甲公司',companies:[{name:'甲公司'},{name:'乙公司'}]});
  $('#sample-statistics-scope-mode').handlers.change({target:{value:'batch'}});
  await new Promise(resolve=>setImmediate(resolve));
  await $('#sample-statistics-body').handlers.click({target:{closest:selector=>selector === '[data-stat-drill]'
    ? {dataset:{statDrill:'errors',statChannel:'qingtong_template',statRequirement:'甲公章'}} : null}});
  $('#sample-statistics-next').handlers.click();
  assert.match($('#sample-statistics-body').innerHTML,/sample7.jpg/);
  assert.doesNotMatch($('#sample-statistics-body').innerHTML,/sample0.jpg/);
  await $('#sample-statistics-body').handlers.click({target:{closest:selector=>selector === '[data-stat-view]'
    ? {dataset:{statView:'sample7.jpg'}} : null}});
  assert.equal(opened[0][0],1006);
  assert.deepEqual(opened[0][1].browseIds,[1090,1021,1700,1045,1018,1012,1006]);
  assert.equal(typeof opened[0][1].isCurrent,'function');
  assert.equal($('#sample-statistics-dialog').open,true);
});

test('overview and detail views stay separate while preserving filters, pagination and batch', async () => {
  const items = Array.from({length:13},(_,index)=>({
    ...row(index === 12 ? '乙公司' : '甲公司',cell(index % 2 ? '错误' : '正确','匹配'),cell('正确'),cell('正确')),
    filename:`sample${index}.jpg`,job_status:'succeeded',expected_seal:'匹配',requirement:'客户甲公章',
  }));
  const {$,sections,types,controller,exports,requests} = statisticsHarness(items);
  await controller.show({company:'甲公司',companies:[{name:'甲公司'},{name:'乙公司'}]});
  assert.match($('#sample-statistics-body').innerHTML,/sample-stat-scheme-table/);
  assert(!$('#sample-statistics-body').innerHTML.includes('class="sample-stat-table"'));
  assert.match($('#sample-statistics-body').innerHTML,/错误类型分布/);
  assert.match($('#sample-statistics-body').innerHTML,/问题集中在哪里/);
  assert.match($('#sample-statistics-summary').innerHTML,/已标注/);
  assert.match($('#sample-statistics-summary').innerHTML,/参与评测/);
  assert(!$('#sample-statistics-body').innerHTML.includes('sample-stat-coverage'));
  assert.equal($('#sample-statistics-pagination').hidden,true);
  sections[1].handlers.click();
  assert.match($('#sample-statistics-body').innerHTML,/<table/);
  assert(!$('#sample-statistics-body').innerHTML.includes('sample-stat-scheme-table'));
  assert.equal($('#sample-statistics-footer').hidden,false);
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

test('error categories and channel drill-down use the same rows and preserve explicit negative annotations', () => {
  const items = [
    {...row('甲',cell('错误','匹配'),cell('正确','不匹配'),cell('未运行')),expected_seal:'不匹配',requirement:'甲公章'},
    {...row('甲',cell('正确','匹配'),cell('错误','不匹配'),cell('错误','部分匹配')),expected_seal:'匹配',requirement:'甲公章'},
    {...row('乙',cell('未识别'),cell('失败'),cell('待标注')),expected_seal:null,job_status:'succeeded'},
  ];
  assert.deepEqual(filterStatistics(items,{filter:'false_accept'}),[items[0]]);
  assert.deepEqual(filterStatistics(items,{filter:'false_reject',channel:'qingtong_template'}),[]);
  assert.deepEqual(filterStatistics(items,{filter:'false_reject',channel:'qingtong_ocr'}),[items[1]]);
  assert.deepEqual(filterStatistics(items,{filter:'partial',requirement:'甲公章'}),[items[1]]);
  assert.deepEqual(filterStatistics(items,{filter:'pending_truth'}),[items[2]]);
  assert.deepEqual(filterStatistics(items,{filter:'failed',channel:'qingtong_template'}),[]);
  assert.deepEqual(filterStatistics(items,{filter:'unrecognized',channel:'qingtong_template'}),[items[2]]);
});

test('date cohorts and errors never treat a blank annotation as unlabeled', () => {
  const items = [
    {expected_date:'2026-01-01',date:{state:'错误',value:'2026-01-02'}},
    {expected_date:'2026-01-01',date:{state:'未识别',value:''}},
    {expected_date:'',date:{state:'错误',value:'2026-01-01'}},
    {expected_date:'',date:{state:'正确',value:''}},
    {expected_date:null,date:{state:'待标注',value:'2026-01-01'}},
  ];
  assert.deepEqual(filterStatistics(items,{tab:'date',filter:'wrong_date'}),[items[0]]);
  assert.deepEqual(filterStatistics(items,{tab:'date',filter:'date_missed'}),[items[1]]);
  assert.deepEqual(filterStatistics(items,{tab:'date',filter:'date_spurious'}),[items[2]]);
  assert.deepEqual(filterStatistics(items,{tab:'date',filter:'date_blank'}),[items[2],items[3]]);
  assert.deepEqual(filterStatistics(items,{tab:'date',filter:'pending_truth'}),[items[4]]);
});

test('scope explains latest tests, separates batch selection and drill-down exports the selected requirement and channel', async () => {
  const item = {...row('甲',cell('错误','匹配'),cell('正确','不匹配'),cell('未运行')),
    filename:'sample1.jpg',job_status:'succeeded',expected_seal:'不匹配',requirement:'甲 & 乙公章',
    task_id:'batch1',task_name:'样单测试',tested_at:'2026-10-23T10:30:00+08:00'};
  const {$,controller,exports,requests,sections} = statisticsHarness([item]);
  await controller.show({company:'',companies:[{name:'甲'}]});
  assert.equal($('#sample-statistics-batch-label').hidden,true);
  assert.match($('#sample-statistics-scope-note').textContent,/不会为某一路单独补取旧结果/);
  assert.match($('#sample-statistics-batch').innerHTML,/optgroup label="单张样单测试"/);
  $('#sample-statistics-scope-mode').handlers.change({target:{value:'batch'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.at(-1),'batch1');
  assert.equal($('#sample-statistics-batch-label').hidden,false);
  assert.match($('#sample-statistics-scope-note').textContent,/仅统计/);
  await $('#sample-statistics-body').handlers.click({target:{closest:selector=>selector === '[data-stat-drill]'
    ? {dataset:{statDrill:'false_accept',statChannel:'qingtong_template',statRequirement:'甲 & 乙公章'}} : null}});
  assert.equal($('#sample-statistics-dialog').dataset.section,'details');
  assert.match($('#sample-statistics-body').innerHTML,/签章要求：甲 &amp; 乙公章/);
  assert.match($('#sample-statistics-body').innerHTML,/判定：匹配/);
  assert.match($('#sample-statistics-page').textContent,/共 1 张/);
  await $('#sample-statistics-export').handlers.click();
  const url = new URL(exports.at(-1));
  assert.equal(url.searchParams.get('filter'),'false_accept');
  assert.equal(url.searchParams.get('channel'),'qingtong_template');
  assert.equal(url.searchParams.get('requirement'),'甲 & 乙公章');
  sections[0].handlers.click();
  await $('#sample-statistics-export').handlers.click();
  assert.equal(new URL(exports.at(-1)).searchParams.get('channel'),'');
  $('#sample-statistics-scope-mode').handlers.change({target:{value:'latest'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.at(-1),'');
  assert.equal($('#sample-statistics-batch-label').hidden,true);
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

test('changing batch clears a channel that is absent from the new scheme', async () => {
  const item = {...row('甲',cell('错误','匹配'),cell('正确','不匹配'),cell('未运行')),
    filename:'sample.jpg',job_status:'succeeded',expected_seal:'不匹配',requirement:'甲公章'};
  const {$,controller,exports} = statisticsHarness([item],{dataForTask:(taskId,data)=>({
    ...data,columns:taskId ? [{key:'local:paddle_seal',label:'本地模型'}] : data.columns,
  })});
  await controller.show({company:'',companies:[]});
  await $('#sample-statistics-body').handlers.click({target:{closest:selector=>selector === '[data-stat-drill]'
    ? {dataset:{statDrill:'errors',statChannel:'qingtong_template'}} : null}});
  $('#sample-statistics-batch').handlers.change({target:{value:'batch1'}});
  await new Promise(resolve=>setImmediate(resolve));
  await $('#sample-statistics-export').handlers.click();
  assert.equal(new URL(exports.at(-1)).searchParams.get('channel'),'');
  assert.match($('#sample-statistics-page').textContent,/共 1 张/);
});

test('disagreement shortcut only counts supported decisions, matching its detail filter', async () => {
  const items = [
    {...row('甲',cell('错误','需人工复核'),cell('正确','匹配'),cell('未运行')),expected_seal:'匹配'},
    {...row('甲',cell('错误','不匹配'),cell('正确','匹配'),cell('未运行')),expected_seal:'匹配'},
  ];
  const {$,controller} = statisticsHarness(items);
  await controller.show({company:'',companies:[]});
  assert.match($('#sample-statistics-body').innerHTML,/data-stat-drill="remote"[^>]*aria-label="查看清瞳两路不一致，1 张"/);
  assert.equal(filterStatistics(items,{filter:'remote'}).length,1);
});

test('summary anomalies open filtered details and refresh waits until the sample returns', async () => {
  const items = Array.from({length:8},(_,index)=>({
    ...row('甲',cell('错误','不匹配'),cell('正确','匹配'),cell('正确','匹配')),
    filename:`sample${index}.jpg`,result_id:index+1,job_status:'succeeded',expected_seal:'匹配',
  }));
  const {$,controller,requests,exports} = statisticsHarness(items);
  await controller.show({company:'甲',companies:[{name:'甲'}]});
  await $('#sample-statistics-summary').handlers.click({target:{closest:selector=>selector === '[data-stat-drill]'
    ? {dataset:{statDrill:'errors'}} : null}});
  assert.equal($('#sample-statistics-dialog').dataset.section,'details');
  $('#sample-statistics-next').handlers.click();
  const content = $('#sample-statistics-body').innerHTML;
  await $('#sample-statistics-body').handlers.click({target:{closest:selector=>selector === '[data-stat-view]'
    ? {dataset:{statView:'sample7.jpg'}} : null}});
  await controller.refresh();
  assert.equal(requests.length,1);
  assert.equal($('#sample-statistics-body').innerHTML,content);
  $('#review-dialog').open = false;
  $('#review-dialog').handlers.close();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,2);
  assert.match($('#sample-statistics-page').textContent,/2 \/ 2/);
  await $('#sample-statistics-export').handlers.click();
  assert.equal(new URL(exports.at(-1)).searchParams.get('filter'),'errors');
});
