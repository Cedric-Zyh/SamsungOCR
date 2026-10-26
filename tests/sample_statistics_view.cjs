const test = require('node:test');
const assert = require('node:assert/strict');
const {statisticsOverview, statisticsSummary, problemAnnotationsPanel} = require('../static/modules/samples/statistics_view.mjs');
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

test('human problem distribution deduplicates tags and samples without changing scheme evaluation', () => {
  const first = sample('a', '正确', {filename:'one.png', problem_review:{status:'issues', types:['日期读错', '日期读错', '<印章&问题>']},
    sample_features:['遮挡', '遮挡'], legacy_problem_types:['旧错误']});
  const rows = [first, first, sample('a', '错误', {filename:'two.png', problem_review:{status:'clear', types:['不应计入']}}),
    sample('a', '正确', {filename:'three.png'})];
  const panel = problemAnnotationsPanel(rows);
  assert.match(panel, /已发现问题 1 张/);
  assert.match(panel, /尚未标注 1/);
  assert.match(panel, /已核对无问题 1/);
  assert.match(panel, /日期读错<\/th><td>1<\/td>/);
  assert.match(panel, /遮挡<\/th><td>1<\/td>/);
  assert.match(panel, /&lt;印章&amp;问题&gt;/);
  assert.match(panel, /1 张样单有未关联测试的旧标记/);
  assert.match(panel, /不改变自动评测结果或正确率/);
  assert.doesNotMatch(panel, /不应计入|<印章/);
  const before = statisticsOverview(rows, options([{key:'a', label:'方案 A'}]));
  const after = statisticsOverview(rows.map(({problem_review, sample_features, ...row}) => row), options([{key:'a', label:'方案 A'}]));
  const schemeTable = html => html.slice(0, html.indexOf('<details'));
  assert.equal(schemeTable(before), schemeTable(after));
});

test('manual seal labels distinguish remote and local channels while preserving old unspecific tags', () => {
  const both = sample('local:paddle_v6', '正确', {filename:'one.png', problem_review:{status:'issues', types:[
    '清瞳印章判定错误', '本地印章判定错误', '清瞳印章判定错误',
  ]}});
  const onePanel = problemAnnotationsPanel([both, both]);
  assert.match(onePanel, /已发现问题 1 张/);
  assert.match(onePanel, /清瞳印章判定错误<\/th><td>1<\/td>/);
  assert.match(onePanel, /本地印章判定错误<\/th><td>1<\/td>/);
  const old = sample('local:paddle_v6', '正确', {filename:'old.png',
    problem_review:{status:'issues', types:['印章判定错误']}});
  const panel = problemAnnotationsPanel([both, old]);
  assert.match(panel, /已发现问题 2 张/);
  assert.match(panel, /<th scope="row">印章判定错误<\/th><td>1<\/td>/);
  const config = options([{key:'local:paddle_v6', label:'本地印章'}]);
  const schemeTable = html => html.slice(0, html.indexOf('<details'));
  assert.equal(schemeTable(statisticsOverview([both], config)),
    schemeTable(statisticsOverview([{...both, problem_review:undefined}], config)));
});
