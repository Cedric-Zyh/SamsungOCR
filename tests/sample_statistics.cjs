const test = require('node:test');
const assert = require('node:assert/strict');
const {summarizeCells, filterStatistics, statisticsCell} = require('../static/modules/samples/statistics.mjs');
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
