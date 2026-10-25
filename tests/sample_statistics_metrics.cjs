const test = require('node:test');
const assert = require('node:assert/strict');
const {getCell, rowAnnotated, summarizeCoverage, summarizeChannel, groupStatistics,
  compareChannels, summarizeDisagreements} = require('../static/modules/samples/statistics_metrics.mjs');

const cell = (state, decision = '', value = '章') => ({state, decision, value});
const sample = (expected, seals = {}, extra = {}) => ({company: '甲公司', requirement: '客户收货章',
  job_status: 'succeeded', expected_seal: expected, expected_date: null, seals, ...extra});

test('coverage counts each sample once, keeps blank-date annotations and separates denominators', () => {
  const rows = [
    sample('匹配', {a: cell('正确'), b: cell('错误')}),
    sample('不匹配', {a: cell('错误'), b: cell('失败')}, {job_status: 'failed', expected_date: ''}),
    sample(null, {a: cell('待标注')}, {expected_date: '2026-10-23', date: cell('正确')}),
    sample(null, {}, {job_status: ''}),
    sample('匹配', {a: cell('测试中')}, {job_status: 'queued'}),
  ];
  assert.deepEqual(summarizeCoverage(rows), {total: 5, tested: 3, untested: 1, pending: 1, failed: 1,
    annotated: 3, unannotated: 2, evaluable: 2, anomalies: 2, positive: 2, negative: 1});
  const dates = summarizeCoverage(rows, 'date');
  assert.equal(dates.annotated, 2);
  assert.equal(dates.positive, 1);
  assert.equal(dates.negative, 1);
  assert.equal(dates.evaluable, 1);
  assert.equal(rowAnnotated({expected_date: ''}, 'date'), true);
  assert.equal(rowAnnotated({}, 'date'), false);
  assert.equal(rowAnnotated({expected_seal: '部分匹配'}), false);
  assert.equal(getCell({}, 'absent').state, '未测试');
  assert.equal(getCell({job_status: 'succeeded'}, 'absent').state, '未运行');
  assert.deepEqual(getCell({date: {state: '正确'}}, 'date'), {state: '正确', value: '', decision: '', note: ''});
});

test('seal classifications require scored annotations and never turn unavailable readings into false decisions', () => {
  const rows = [
    sample('不匹配', {a: cell('错误', '匹配')}),
    sample('匹配', {a: cell('错误', '不匹配')}),
    sample('匹配', {a: cell('错误', '部分匹配')}),
    sample('匹配', {a: cell('错误', '需人工复核')}),
    sample('不匹配', {a: cell('未识别', '匹配', '')}),
    sample('匹配', {a: cell('失败', '不匹配')}),
    sample('不匹配', {a: cell('待标注', '匹配')}),
    sample(null, {a: cell('待标注', '部分匹配')}),
    sample('匹配', {a: cell('正确', '匹配')}),
    sample('匹配', {}),
  ];
  const stats = summarizeChannel(rows, 'a');
  assert.equal(stats.total, 7);
  assert.equal(stats.accuracy, 1 / 7);
  assert.equal(stats.counts['未运行'], 1);
  assert.equal(stats['正确'], stats.correct);
  assert.deepEqual(stats.errorTypes, {falseAccept: 1, falseReject: 1, partial: 1, other: 1,
    dateWrong: 0, dateMissing: 0, blankFalseRead: 0, failure: 1});
  assert.equal(summarizeChannel([], 'a').accuracy, null);
  assert.equal(summarizeChannel([sample(null, {})], 'a').total, 0);
});

test('date cohorts expose real-date performance separately from blanks and classify all date error types', () => {
  const rows = [
    sample(null, {}, {expected_date: '2026-10-23', date: cell('正确', '', '2026-10-23')}),
    sample(null, {}, {expected_date: '2026-10-23', date: cell('错误', '', '2026-10-22')}),
    sample(null, {}, {expected_date: '2026-10-23', date: cell('未识别', '', '')}),
    sample(null, {}, {expected_date: '2026-10-23', date: cell('失败', '', '')}),
    sample(null, {}, {expected_date: '2026-10-23', date: cell('未运行', '', '')}),
    sample(null, {}, {expected_date: '', date: cell('正确', '', '')}),
    sample(null, {}, {expected_date: '', date: cell('错误', '', '2026-10-23')}),
    sample(null, {}, {expected_date: null, date: cell('待标注', '', '2026-10-23')}),
  ];
  const stats = summarizeChannel(rows, 'date', 'date');
  assert.equal(stats.total, 6);
  assert.equal(stats.accuracy, 2 / 6);
  assert.equal(stats.cohorts.dated.samples, 5);
  assert.equal(stats.cohorts.dated.total, 4);
  assert.equal(stats.cohorts.dated.accuracy, 0.25);
  assert.equal(stats.cohorts.blank.samples, 2);
  assert.equal(stats.cohorts.blank.total, 2);
  assert.equal(stats.cohorts.blank.accuracy, 0.5);
  assert.equal(stats.errorTypes.dateWrong, 1);
  assert.equal(stats.errorTypes.dateMissing, 1);
  assert.equal(stats.errorTypes.blankFalseRead, 1);
  assert.equal(stats.errorTypes.failure, 1);
  const empty = summarizeChannel([], 'date', 'date');
  assert.equal(empty.cohorts.dated.accuracy, null);
  assert.equal(empty.cohorts.blank.accuracy, null);
});

test('grouped metrics count anomalous rows once and sort actionable groups first', () => {
  const rows = [
    sample('匹配', {a: cell('正确'), b: cell('正确')}),
    sample('匹配', {a: cell('错误'), b: cell('失败')}, {company: '<乙>', requirement: ''}),
    sample(null, {}, {company: '<乙>', requirement: ''}),
  ];
  const grouped = groupStatistics(rows, {columns: [{key: 'a', label: '方案 A'}, {key: 'b', label: '方案 B'}]});
  assert.equal(grouped[0].label, '<乙>');
  assert.equal(grouped[0].total, 2);
  assert.equal(grouped[0].anomalies, 1);
  assert.equal(grouped[0].annotated, 1);
  assert.equal(grouped[0].channels[0].label, '方案 A');
  assert.equal(grouped[0].channels[0].accuracy, 0);
  assert.equal(groupStatistics(rows, {by: 'requirement'})[0].label, '未标注签章要求');
  assert.deepEqual(groupStatistics([]), []);
});

test('shared comparison uses exactly the same scored samples, including missing and failed outcomes', () => {
  const rows = [
    sample('匹配', {a: cell('正确'), b: cell('错误')}),
    sample('匹配', {a: cell('正确'), b: cell('未运行')}),
    sample('匹配', {a: cell('失败'), b: cell('未识别')}),
    sample('匹配', {a: cell('未运行'), b: cell('正确')}),
  ];
  const compared = compareChannels(rows, [{key: 'a', label: '甲'}, {key: 'b', label: '乙'}]);
  assert.equal(compared.total, 2);
  assert.equal(compared.excluded, 2);
  assert.deepEqual(compared.channels.map(channel => channel.total), [2, 2]);
  assert.equal(compared.channels[0].accuracy, 0.5);
  assert.equal(compared.channels[1].accuracy, 0);
  assert.equal(compared.channels[0].label, '甲');
  assert.equal(compareChannels(rows, ['a', 'missing']).total, 0);
  assert.equal(compareChannels(rows, []).total, 0);
});

test('disagreements compare valid decisions and expose each side correctness only on disagreements', () => {
  const remoteA = 'qingtong_template', remoteB = 'qingtong_ocr', local = 'local:model';
  const rows = [
    sample('匹配', {[remoteA]: cell('正确', '匹配'), [remoteB]: cell('错误', '不匹配'), [local]: cell('正确', '匹配')}),
    sample('不匹配', {[remoteA]: cell('错误', '匹配'), [remoteB]: cell('正确', '不匹配'), [local]: cell('正确', '不匹配')}),
    sample('匹配', {[remoteA]: cell('错误', '不匹配'), [remoteB]: cell('错误', '部分匹配'), [local]: cell('错误', '部分匹配')}),
    sample('匹配', {[remoteA]: cell('正确', '匹配'), [remoteB]: cell('正确', '匹配'), [local]: cell('未运行')}),
    sample('匹配', {[remoteA]: cell('失败', '匹配'), [remoteB]: cell('错误', '不匹配'), [local]: cell('未识别', '匹配')}),
    sample(null, {[remoteA]: cell('待标注', '匹配'), [remoteB]: cell('待标注', '不匹配')}),
  ];
  const result = summarizeDisagreements(rows, [{key: remoteA}, {key: remoteB}, {key: local}]);
  assert.deepEqual(result.remote, {leftKey: remoteA, rightKey: remoteB, total: 4, disagreements: 3,
    onlyLeftCorrect: 1, onlyRightCorrect: 1, bothWrong: 1, bothCorrect: 0});
  assert.equal(result.local.length, 2);
  assert.equal(result.local[0].total, 3);
  assert.equal(result.local[0].disagreements, 2);
  assert.equal(result.local[1].disagreements, 1);
  assert.equal(summarizeDisagreements([]).remote.total, 0);
});
