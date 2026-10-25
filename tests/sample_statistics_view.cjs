const test = require('node:test');
const assert = require('node:assert/strict');
const {statisticsOverview, statisticsSummary} = require('../static/modules/samples/statistics_view.mjs');
const {statisticsDistribution} = require('../static/modules/samples/statistics.mjs');

const options = columns => ({tab:'seal', columns, groupBy:'company', showAllGroups:false,
  distribution:statisticsDistribution});
const sample = (key, state = '错误', extras = {}) => ({company:'甲', requirement:'收货章',
  expected_seal:'匹配', job_status:'succeeded', seals:{[key]:{state, decision:'不匹配'}}, ...extras});

test('scheme comparison distinguishes no evaluable samples from zero correct out of seven', () => {
  const config = options([{key:'a', label:'方案 A'}]);
  const empty = statisticsOverview([], config);
  assert.match(empty, /class="sample-stat-accuracy"><strong>—<\/strong>/);
  assert.doesNotMatch(empty, /0\.0%/);
  const zero = statisticsOverview(Array.from({length:7}, () => sample('a')), config);
  assert.match(zero, /class="sample-stat-accuracy"><strong>0\.0%<\/strong>/);
  assert.match(zero, /class="sample-stat-evaluated"><strong>0<\/strong><span> \/ 7<\/span>/);
  assert.match(zero, /data-stat-drill="wrong"[^>]+data-stat-channel="a"[^>]*>7 张 /);
});

test('problem drill links retain the scheme and escaped company or requirement', () => {
  const key = 'local:"<model>&';
  const label = '<img src=x onerror=alert(1)>';
  const company = '客户"<&';
  const requirement = '收货"<&';
  const rows = [sample(key, '错误', {company, requirement})];
  const config = options([{key, label}]);
  const companyView = statisticsOverview(rows, config);
  assert.match(companyView, /data-stat-drill="wrong" data-stat-channel="local:&quot;&lt;model&gt;&amp;"/);
  assert.match(companyView, /data-stat-company="客户&quot;&lt;&amp;"/);
  assert.match(companyView, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(companyView, /<img|data-stat-channel="local:"/);
  const requirementView = statisticsOverview(rows, {...config, groupBy:'requirement'});
  assert.match(requirementView, /data-stat-requirement="收货&quot;&lt;&amp;"/);
});

test('summary problem counts drill into all schemes, with no links for zero counts', () => {
  const rows = [sample('a', '错误', {seals:{a:{state:'错误'}, b:{state:'失败'}}}),
    sample('a', '待标注', {expected_seal:null, job_status:''})];
  const html = statisticsSummary(rows, 'seal');
  assert.match(html, /data-stat-drill="errors" data-stat-channel=""[^>]*>1 张 /);
  assert.match(html, /data-stat-drill="pending_truth" data-stat-channel=""/);
  assert.match(html, /data-stat-drill="untested" data-stat-channel=""/);
  assert.doesNotMatch(statisticsSummary([], 'seal'), /data-stat-drill/);
});
