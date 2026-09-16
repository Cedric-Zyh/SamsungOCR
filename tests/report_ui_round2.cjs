const assert = require('node:assert/strict');
const test = require('node:test');
const {createReport} = require('../static/modules/report.mjs');

function harness(api) {
  const nodes = new Map(), reportState = {}, resultsState = {};
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {innerHTML: '', textContent: '', dataset: {}, attributes: {},
      setAttribute(key, value) { this.attributes[key] = value; }, addEventListener() {}});
    return nodes.get(selector);
  };
  const controller = createReport({ui: {$, toast() {}}, reportState, resultsState, api});
  return {$, controller, reportState, resultsState};
}

test('unassessed dimensions show a dash while genuine zero label coverage stays 0%', async () => {
  const h = harness(async () => ({accuracy: {field_accuracy: 0, field_total: 0,
    product_cell_accuracy: 0, product_cell_total: 0, date_accuracy: 0, date_total: 0,
    seal_conclusion_accuracy: 0, seal_total: 0}, dataset: {images: 12, labeled: 0, coverage: 0}}));
  await h.controller.loadReport();
  const cards = h.$('#kpi-grid').innerHTML;
  assert.equal((cards.match(/<strong>—<\/strong>/g) || []).length, 4);
  assert.match(cards, /评测真值覆盖<\/span><strong>0%/);
  assert.doesNotMatch(cards, /检出率 0%/);
  assert.match(h.$('#field-accuracy').innerHTML, /暂无字段评测数据/);
  assert.match(h.$('#error-stats').innerHTML, /暂无回单记录/);
});

test('reported accuracy includes denominators and distinguishes incorrect samples from no samples', async () => {
  const h = harness(async () => ({accuracy: {field_accuracy: 0, field_correct: 0, field_total: 7,
    product_cell_accuracy: 0.75, product_cell_correct: 9, product_cell_total: 12,
    date_accuracy: 0.5, date_correct: 1, date_total: 2, date_detection_rate: 1, date_present_detected: 1, date_present_total: 1,
    seal_conclusion_accuracy: 1, seal_correct: 2, seal_total: 2,
    field_by_name: [{field: '正确字段', accuracy: 1, correct: 3, total: 3}, {field: '<不匹配>', accuracy: 0, correct: 0, total: 4}]},
    backend_comparison: [{label: '未评测方式', samples: 0, field_accuracy: 0, field_total: 0, date_total: 0},
      {label: '有误的方式', samples: 2, field_accuracy: 0, field_total: 7, product_cell_total: 12, product_cell_accuracy: 0.75, timed_samples: 2, average_seconds: 3.5}]}));
  await h.controller.loadReport();
  const cards = h.$('#kpi-grid').innerHTML, rows = h.$('#field-accuracy').innerHTML, backends = h.$('#backend-comparison').innerHTML;
  assert.match(cards, /已标注字段准确率<\/span><strong>0%<\/strong><small>正确 0 \/ 7 个字段/);
  assert.match(cards, /检出率 100%（1 \/ 1 张有日期样本）/);
  assert(rows.indexOf('&lt;不匹配&gt;') < rows.indexOf('正确字段'));
  assert.doesNotMatch(rows, /<不匹配>/);
  assert.match(backends, /7 项<\/small><\/dt><dd>0%/);
  assert.match(backends, /0 项<\/small><\/dt><dd>—/);
  assert.match(backends, /数量未提供<\/small><\/dt><dd>—/);
  assert.match(backends, /3.5 秒\/张/);
});

test('missing timing samples differ from a measured duration rounded to zero', async () => {
  const h = harness(async () => ({backend_comparison: [
    {label: '未记录耗时', samples: 2, timed_samples: 0, average_seconds: 0},
    {label: '旧响应无耗时数量', samples: 2, average_seconds: 0},
    {label: '快速完成', samples: 2, timed_samples: 2, average_seconds: 0},
  ]}));
  await h.controller.loadReport();
  const backends = h.$('#backend-comparison').innerHTML;
  assert.equal((backends.match(/暂无耗时数据/g) || []).length, 2);
  assert.equal((backends.match(/0 秒\/张/g) || []).length, 1);
});

test('error bars preserve counts, sort high to low and explain their scope', async () => {
  const h = harness(async () => ({total_results: 4, error_types: [{type: '日期问题', count: 2}, {type: '<印章&问题>', count: 5}]}));
  await h.controller.loadReport();
  const errors = h.$('#error-stats').innerHTML;
  assert(errors.indexOf('&lt;印章&amp;问题&gt;') < errors.indexOf('日期问题'));
  assert.match(errors, /5 次/); assert.match(errors, /width:40%/);
  assert.match(errors, /同一回单可能计入多项/);
  assert.doesNotMatch(errors, /<印章/);
});

test('refreshes coalesce, preserve the previous report on failure and recover on retry', async () => {
  let resolve, reject, calls = 0;
  const h = harness(() => { calls++; return new Promise((yes, no) => { resolve = yes; reject = no; }); });
  const initial = h.controller.loadReport();
  assert.equal(initial, h.controller.loadReport()); assert.equal(calls, 1);
  assert.equal(h.$('#refresh-report').disabled, true);
  assert.equal(h.$('#kpi-grid').attributes['aria-busy'], 'true');
  resolve({total_results: 20, review_counts: {'待复核': 8}}); await initial;
  const prior = h.$('#kpi-grid').innerHTML, loadedAt = h.reportState.reportLoadedAt;
  const failed = h.controller.loadReport();
  assert.equal(h.$('#kpi-grid').innerHTML, prior);
  assert.match(h.$('#report-status').textContent, /上次结果/);
  reject(Error('断线')); await assert.rejects(failed, /断线/);
  assert.equal(h.$('#kpi-grid').innerHTML, prior); assert.equal(h.reportState.reportLoadedAt, loadedAt);
  assert.match(h.$('#report-status').textContent, /保留上次统计/);
  assert.equal(h.$('#report-status').dataset.tone, 'error');
  assert.equal(h.$('#refresh-report').disabled, false); assert.equal(h.reportState.reportPromise, null);
  const retry = h.controller.loadReport(); resolve({total_results: 20, review_counts: {'待复核': 7}}); await retry;
  assert.match(h.$('#kpi-grid').innerHTML, /待人工复核<\/span><strong>7/);
  assert.equal(h.$('#report-status').dataset.tone, 'neutral');
});

test('first-load errors replace indefinite skeletons, and concurrent edits leave the report stale', async () => {
  let reject, resolve;
  const h = harness(() => new Promise((yes, no) => { resolve = yes; reject = no; }));
  h.$('#kpi-grid').innerHTML = '<div class="skeleton"></div>';
  const failure = h.controller.loadReport(); reject(Error('暂不可用'));
  await assert.rejects(failure, /暂不可用/);
  assert.doesNotMatch(h.$('#kpi-grid').innerHTML, /skeleton/);
  assert.match(h.$('#kpi-grid').innerHTML, /刷新统计/);
  assert.equal(h.$('#kpi-grid').attributes['aria-busy'], 'false');
  assert.equal(h.reportState.reportLoadedAt, undefined);
  const retry = h.controller.loadReport(); h.resultsState.resultRevision = 1; resolve({}); await retry;
  assert.equal(h.reportState.reportStale, true);
  assert.match(h.$('#report-status').textContent, /回单发生变化/);
});

test('inconsistent coverage and invalid ratios never render misleading percentages or bar widths', async () => {
  const h = harness(async () => ({dataset: {images: 1, labeled: 2, coverage: 2}, accuracy: {field_accuracy: null, field_total: 5,
    field_by_name: [{field: '无效比率', total: 1, accuracy: Infinity}, {field: '缺少比率', total: 1}]}}));
  await h.controller.loadReport();
  assert.doesNotMatch(h.$('#kpi-grid').innerHTML, /200%|<strong>0%/);
  assert.match(h.$('#kpi-grid').innerHTML, /数量范围不一致/);
  assert.doesNotMatch(h.$('#field-accuracy').innerHTML, /Infinity|NaN|undefined%/);
});
