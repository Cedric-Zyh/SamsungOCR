const assert = require('node:assert/strict');
const test = require('node:test');
const {renderSampleAnnotations, collectSampleAnnotations, createSampleAnnotationSubmission} = require('../static/modules/review/sample_annotations.mjs');
const {collectReviewPayload} = require('../static/modules/review/submission.mjs');
const {problemHistoryChanges} = require('../static/modules/review/problem_annotations.mjs');

const decodeAttribute = value => value.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');

function harness({current, prefillNote = true} = {}) {
  const nodes = new Map();
  const $ = selector => {
    if (selector === '[data-truth-prefill-note]' && !prefillNote) return null;
    if (!nodes.has(selector)) {
      const classes = new Set(), listeners = new Map();
      let markup = '', inputs = [];
      nodes.set(selector, {value: '', textContent: '', disabled: false, hidden: true, dataset: {},
        get innerHTML() { return markup; },
        set innerHTML(value) {
          markup = value;
          inputs = [...value.matchAll(/<input[^>]*name="([^"]*)"[^>]*value="([^"]*)"([^>]*)>/g)]
            .map(([, name, value, tail]) => ({name, value: decodeAttribute(value), checked: /\bchecked\b/.test(tail)}));
        },
        querySelectorAll(selector) { return selector === 'input:checked' ? inputs.filter(input => input.checked) : inputs; },
        dispatchEvent(event) { this.fire(event.type); return true; },
        addEventListener(type, callback) { listeners.set(type, [...(listeners.get(type) || []), callback]); },
        fire(type, target = this) { listeners.get(type)?.forEach(callback => callback({target})); },
        listenerCount(type) { return listeners.get(type)?.length || 0; },
        classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)}});
    }
    return nodes.get(selector);
  };
  const notices = [], calls = [], controls = [$('[name=human_note]'), $('[data-save-sample]'), $('[name=machine-evidence]')];
  controls[2].disabled = true;
  const ui = {$, $$: () => controls, toast: message => notices.push(message)};
  const reviewState = {sampleView: true, reviewSaving: false, current: current || {id: 1, fields: {'客户名称': '机器客户'}}};
  let complete, fail, refreshes = 0, histories = 0;
  const save = createSampleAnnotationSubmission({ui, reviewState, services: {samples: {saveAnnotations: (id, payload) => {
    calls.push({id, payload}); return new Promise((resolve, reject) => {complete = resolve; fail = reject;});
  }}}, renderHistory: async () => histories++, refreshVisibleResults: async () => refreshes++});
  return {ui, $, reviewState, save, notices, calls, controls, complete: value => complete(value), fail: error => fail(error), counts: () => ({refreshes, histories})};
}

const truth = () => ({revision: 'revision-1', entry: {fields: {'客户名称': '人工客户', '签章要求': '人工签章要求', '要求到货': '2026-03-27'},
  date_present: true, actual_date: '2026-03-27', seal_should_match: false, human_note: '已有备注', error_type: '印章识别错误'}});
const recognized = () => ({id: 1, fields: {'客户名称': '机器客户', '签章要求': '机器要求', '要求到货': '2026-03-27'},
  date_check: {actual: '2026/3/27', status: '匹配', reliable: true},
  seal_check: {status: '不匹配', reliable: true}});

test('new sample drafts use recognized values without saving or changing annotation and machine evidence', () => {
  const current = recognized(), annotation = {revision: 'r0', entry: {fields: {}}, history: []};
  const beforeCurrent = structuredClone(current), beforeAnnotation = structuredClone(annotation);
  const h = harness({current});
  renderSampleAnnotations(h.ui, h.reviewState, annotation, {});
  const payload = collectSampleAnnotations(h.ui, {}, 'r0');
  assert.deepEqual(payload.fields, current.fields);
  assert.equal(payload.date_present, true);
  assert.equal(payload.actual_date, '2026-03-27');
  assert.equal(payload.seal_should_match, false);
  assert.equal(payload.human_note, '');
  assert.equal(payload.problem_status, 'unmarked');
  assert.deepEqual(payload.problem_types, []);
  assert.deepEqual(payload.sample_features, []);
  assert(!Object.hasOwn(payload, 'error_type'));
  assert.equal(h.$('[name=truth_actual_date]').disabled, false);
  for (const name of ['truth_customer', 'truth_requirement', 'truth_required_date', 'truth_date_present', 'truth_actual_date', 'truth_seal_should_match']) {
    assert.equal(h.$(`[name=${name}]`).dataset.annotationSource, 'recognition', name);
  }
  assert.equal(h.$('[data-truth-prefill-note]').hidden, false);
  assert.equal(h.$('[data-truth-prefill-note]').textContent, '已根据本次识别结果带入 6 项，请核对后保存。');
  assert.deepEqual(current, beforeCurrent);
  assert.deepEqual(annotation, beforeAnnotation);
  assert.equal(h.calls.length, 0);
  assert.equal(h.reviewState.reviewDirty, undefined);
  assert.deepEqual(h.counts(), {refreshes: 0, histories: 0});
});

test('editing suggestions clears their recognition markers and updates the note without duplicate listeners', () => {
  const h = harness({current: recognized()}), annotation = {revision: 'r0', entry: {}};
  renderSampleAnnotations(h.ui, h.reviewState, annotation, {});
  renderSampleAnnotations(h.ui, h.reviewState, annotation, {});
  const customer = h.$('[name=truth_customer]');
  assert.equal(customer.listenerCount('input'), 1);
  assert.equal(customer.listenerCount('change'), 1);
  customer.value = '手动修正客户';
  customer.fire('input');
  assert.equal(customer.dataset.annotationSource, undefined);
  assert.equal(h.$('[data-truth-prefill-note]').textContent, '已根据本次识别结果带入 5 项，请核对后保存。');
  customer.fire('change');
  assert.equal(h.$('[data-truth-prefill-note]').textContent, '已根据本次识别结果带入 5 项，请核对后保存。');
  for (const name of ['truth_requirement', 'truth_required_date', 'truth_date_present', 'truth_actual_date', 'truth_seal_should_match']) {
    h.$(`[name=${name}]`).fire('change');
  }
  assert.equal(h.$('[data-truth-prefill-note]').hidden, true);
  assert.equal(h.$('[data-truth-prefill-note]').textContent, '');
  assert.equal(customer.value, '手动修正客户');
  assert.equal(h.calls.length, 0);
});

test('human labels including explicit blanks and negative decisions replace suggestions on rerender', () => {
  const h = harness({current: recognized()});
  renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {});
  const labeled = {revision: 'r1', history: [{changed_at: '2026-03-28 10:00'}], entry: {
    fields: {'客户名称': '人工客户', '签章要求': '', '要求到货': ''},
    date_present: false, actual_date: '', seal_should_match: false, human_note: '', error_type: '',
  }};
  renderSampleAnnotations(h.ui, h.reviewState, labeled, {});
  assert.equal(h.$('[name=truth_customer]').value, '人工客户');
  assert.equal(h.$('[name=truth_requirement]').value, '');
  assert.equal(h.$('[name=truth_required_date]').value, '');
  assert.equal(h.$('[name=truth_date_present]').value, 'false');
  assert.equal(h.$('[name=truth_actual_date]').value, '');
  assert.equal(h.$('[name=truth_actual_date]').disabled, true);
  assert.equal(h.$('[name=truth_seal_should_match]').value, 'false');
  assert.equal(h.$('[data-truth-prefill-note]').hidden, true);
  assert.equal(h.$('[data-truth-prefill-note]').textContent, '');
  for (const name of ['truth_customer', 'truth_requirement', 'truth_required_date', 'truth_date_present', 'truth_actual_date', 'truth_seal_should_match']) {
    assert.equal(h.$(`[name=${name}]`).dataset.annotationSource, undefined, name);
  }
  assert.equal(h.calls.length, 0);
});

test('an existing actual date takes priority and explicit empty date labels are not filled from recognition', () => {
  const h = harness({current: recognized()});
  renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {actual_date: '2026-03-28'}}, {});
  assert.equal(h.$('[name=truth_actual_date]').value, '2026-03-28');
  assert.equal(h.$('[name=truth_date_present]').value, 'true');
  assert.equal(h.$('[name=truth_actual_date]').dataset.annotationSource, undefined);
  for (const entry of [{actual_date: ''}, {date_present: ''}, {date_present: null}, {date_present: false}]) {
    renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry}, {});
    assert.equal(h.$('[name=truth_actual_date]').value, '');
    assert.equal(h.$('[name=truth_date_present]').value, entry.date_present === false ? 'false' : '');
    assert.equal(h.$('[name=truth_actual_date]').disabled, true);
    assert.equal(h.$('[name=truth_actual_date]').dataset.annotationSource, undefined);
  }
});

test('cleared enums stay unlabelled after a prior save even when their keys were removed by the server', () => {
  const h = harness({current: recognized()});
  renderSampleAnnotations(h.ui, h.reviewState, {
    revision: 'r1', entry: {}, history: [{changed_at: '2026-03-28 10:00'}],
  }, {});
  assert.equal(h.$('[name=truth_date_present]').value, '');
  assert.equal(h.$('[name=truth_actual_date]').value, '');
  assert.equal(h.$('[name=truth_actual_date]').disabled, true);
  assert.equal(h.$('[name=truth_seal_should_match]').value, '');
  assert.equal(h.$('[name=truth_customer]').value, '机器客户');
  assert.equal(h.$('[data-truth-prefill-note]').textContent, '已根据本次识别结果带入 3 项，请核对后保存。');
  assert.equal(collectSampleAnnotations(h.ui, {}, 'r1').date_present, null);
  assert.equal(collectSampleAnnotations(h.ui, {}, 'r1').seal_should_match, null);
  for (const value of ['', null, false]) {
    renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {seal_should_match: value}}, {});
    assert.equal(h.$('[name=truth_seal_should_match]').value, value === false ? 'false' : '');
    assert.equal(h.$('[name=truth_seal_should_match]').dataset.annotationSource, undefined);
  }
});

test('invalid or unreliable dates never suggest absence or use the required date as an actual date', () => {
  const base = {actual: '2026-03-27', status: '匹配', reliable: true};
  const checks = [
    {}, {...base, actual: ''}, {...base, actual: '2026-02-30'}, {...base, actual: '2026-03-270'},
    {...base, actual: '识别失败 2026-03-27'}, {...base, actual: '3月27日'},
    {...base, reliable: false}, {...base, reliable: undefined},
    {...base, status: '未执行'}, {...base, status: '未识别'}, {...base, status: '识别失败'},
    {...base, execution_status: 'skipped'}, {...base, execution_status: 'failed'},
  ];
  for (const check of checks) {
    const h = harness({current: {id: 1, fields: {'要求到货': '2026-03-27'}, date_check: check}});
    renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {});
    assert.equal(h.$('[name=truth_date_present]').value, '', JSON.stringify(check));
    assert.equal(h.$('[name=truth_actual_date]').value, '', JSON.stringify(check));
    assert.equal(h.$('[name=truth_actual_date]').disabled, true);
    assert.equal(h.$('[name=truth_actual_date]').dataset.annotationSource, undefined);
    assert.equal(h.calls.length, 0);
  }
  const h = harness({current: {fields: {}, date_check: {...base, actual: '2024年2月29日', status: '不匹配'}}});
  renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {});
  assert.equal(h.$('[name=truth_actual_date]').value, '2024-02-29');
  assert.equal(h.$('[name=truth_date_present]').value, 'true');
});

test('seal suggestions require a reliable explicit verdict or a human-confirmed boolean', () => {
  const cases = [
    [{}, ''], [{status: '部分匹配', reliable: true}, ''], [{status: '匹配', reliable: false}, ''],
    [{status: '不匹配'}, ''], [{status: '未执行', reliable: true}, ''],
    [{status: '识别失败', reliable: true}, ''], [{status: '不匹配', reliable: true, execution_status: 'failed'}, ''],
    [{status: '匹配', reliable: true}, 'true'], [{status: '不匹配', reliable: true}, 'false'],
    [{status: '部分匹配', reliable: false, human_confirmed_match: true}, 'true'],
    [{status: '部分匹配', reliable: false, human_confirmed_match: false}, 'false'],
  ];
  for (const [check, expected] of cases) {
    const h = harness({current: {id: 1, fields: {}, seal_check: check}});
    renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {});
    assert.equal(h.$('[name=truth_seal_should_match]').value, expected, JSON.stringify(check));
    assert.equal(h.$('[data-truth-prefill-note]').hidden, !expected);
    assert.equal(h.calls.length, 0);
  }
});

test('blank recognition text stays blank and older markup without a prefill note remains supported', () => {
  const h = harness({prefillNote: false, current: {id: 1, fields: {'客户名称': '  ', '签章要求': null, '要求到货': 20260327}}});
  assert.doesNotThrow(() => renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {}));
  for (const name of ['truth_customer', 'truth_requirement', 'truth_required_date']) {
    assert.equal(h.$(`[name=${name}]`).value, '');
  }
  assert.equal(h.calls.length, 0);
  for (const value of ['未提供', '未识别', '未执行', '识别失败', 'N/A', '—']) {
    h.reviewState.current.fields = {'客户名称': value, '签章要求': value, '要求到货': value};
    renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {});
    for (const name of ['truth_customer', 'truth_requirement', 'truth_required_date']) {
      assert.equal(h.$(`[name=${name}]`).value, '', value);
      assert.equal(h.$(`[name=${name}]`).dataset.annotationSource, undefined);
    }
  }
});

test('prefilled draft values persist only after an explicit save', async () => {
  const h = harness({current: recognized()});
  renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {}}, {});
  assert.equal(h.calls.length, 0);
  const pending = h.save();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].payload.actual_date, '2026-03-27');
  assert.equal(h.calls[0].payload.seal_should_match, false);
  h.complete({revision: 'r1', entry: h.calls[0].payload, history: [{changed_at: '2026-03-28 10:00'}]});
  await pending;
  assert.equal(h.$('[data-truth-prefill-note]').hidden, true);
  assert.equal(h.reviewState.sampleAnnotationRevision, 'r1');
});

test('sample editor loads human labels, keeps unknown labels distinct from negative labels, and leaves evidence intact', () => {
  const h = harness();
  renderSampleAnnotations(h.ui, h.reviewState, {revision: 'r0', entry: {fields: {}}}, {});
  assert.equal(h.$('[name=truth_seal_should_match]').value, '');
  assert.equal(h.$('[name=truth_date_present]').value, '');
  assert.equal(h.$('[name=truth_actual_date]').disabled, true);
  renderSampleAnnotations(h.ui, h.reviewState, truth(), {});
  assert.equal(h.$('[name=truth_seal_should_match]').value, 'false');
  assert.equal(h.$('[name=truth_actual_date]').disabled, false);
  assert.equal(h.$('[name=truth_customer]').value, '人工客户');
  assert.equal(h.$('[name=human_note]').value, '已有备注');
  assert.equal(h.reviewState.current.fields['客户名称'], '机器客户');
  h.$('[name=truth_date_present]').value = 'false';
  const payload = collectSampleAnnotations(h.ui, {}, 'revision-1');
  assert.equal(payload.date_present, false);
  assert.equal(payload.actual_date, '');
  assert.equal(payload.seal_should_match, false);
  h.$('[name=truth_date_present]').value = '';
  h.$('[name=truth_seal_should_match]').value = '';
  assert.equal(collectSampleAnnotations(h.ui, {}, 'revision-1').seal_should_match, null);
  assert.equal(collectSampleAnnotations(h.ui, {}, 'revision-1').date_present, null);
});

test('sample save blocks duplicates, persists notes, restores disabled evidence, and stays in the same sample', async () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, truth(), {});
  h.reviewState.reviewDirty = true;
  h.$('[name=human_note]').value = '修正备注';
  const pending = h.save();
  assert.equal(h.reviewState.reviewSaving, true);
  assert(h.controls.every(control => control.disabled));
  await h.save(); assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].payload.human_note, '修正备注');
  assert.equal(h.calls[0].payload.revision, 'revision-1');
  h.complete({...truth(), revision: 'revision-2', entry: {...truth().entry, human_note: '修正备注'}});
  await pending;
  assert.equal(h.reviewState.reviewSaving, false);
  assert.equal(h.reviewState.reviewDirty, false);
  assert.equal(h.reviewState.sampleAnnotationRevision, 'revision-2');
  assert.equal(h.reviewState.current.id, 1);
  assert.equal(h.controls[2].disabled, true);
  assert.deepEqual(h.counts(), {refreshes: 1, histories: 1});
});

test('sample conflict keeps all edits and the original revision for safe recovery', async () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, truth(), {});
  h.reviewState.reviewDirty = true;
  h.$('[name=human_note]').value = '尚未保存';
  const pending = h.save(); h.fail(new Error('其他窗口已更新')); await pending;
  assert.equal(h.reviewState.reviewDirty, true);
  assert.equal(h.$('[name=human_note]').value, '尚未保存');
  assert.equal(h.reviewState.sampleAnnotationRevision, 'revision-1');
  assert.equal(h.reviewState.reviewSaving, false);
  assert.equal(h.controls[0].disabled, false);
  assert.match(h.notices[0], /其他窗口/);
  assert.deepEqual(h.counts(), {refreshes: 0, histories: 0});
});

test('receipt payload works with sample-only controls removed and does not erase historical notes', () => {
  const form = Object.fromEntries(['actual_date', 'actual_date_confirmed', 'seal_text', 'seal_confirmed_match'].map(name => [name, {value: ''}]));
  const {payload} = collectReviewPayload(form, '待复核', '需人工复核', 'rev', () => []);
  assert.equal(payload.save_ground_truth, false);
  assert(!Object.hasOwn(payload, 'human_note'));
  assert(!Object.hasOwn(payload, 'error_type'));
});

const problemTruth = overrides => ({...truth(), problem_options: {
  types: ['日期读错', '印章文字缺漏或读错', '原单日期不符', '证据不足待核对'],
  features: ['模糊', '遮挡', '版式变化'],
}, problem_review: {status: 'unmarked', types: []}, sample_features: [], legacy_problem_types: [],
problem_context: {result_id: 1, task_name: '样单测试 10 张', tested_at: '2026-10-25T12:30:00+08:00', methods: ['PaddleOCR', '清瞳']},
...overrides});

function tick(h, container, value, checked = true) {
  const node = h.$(container), input = node.querySelectorAll('input').find(input => input.value === value);
  assert(input, `missing checkbox ${value}`);
  input.checked = checked; node.fire('change', input);
}

test('multiple problems and persistent image features are independently selected and serialized', () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, problemTruth(), {});
  tick(h, '[data-problem-types]', '日期读错');
  tick(h, '[data-problem-types]', '印章文字缺漏或读错');
  tick(h, '[data-sample-features]', '遮挡');
  const payload = collectSampleAnnotations(h.ui, {}, 'revision-1');
  assert.equal(payload.problem_status, 'issues');
  assert.deepEqual(payload.problem_types, ['日期读错', '印章文字缺漏或读错']);
  assert.deepEqual(payload.sample_features, ['遮挡']);
  assert(!Object.hasOwn(payload, 'error_type'));
  assert.match(h.$('[data-problem-context]').textContent, /样单测试 10 张.*2026-10-25 12:30:00/);
  assert.doesNotMatch(h.$('[data-problem-context]').textContent, /PaddleOCR|清瞳/);
  assert.equal(h.$('[data-problem-context]').title, '识别方式：PaddleOCR、清瞳');
});

test('marking clear or unmarked removes problem selections but keeps image features', () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, problemTruth({
    problem_review: {status: 'issues', types: ['日期读错']}, sample_features: ['模糊'],
  }), {});
  const status = h.$('[name=problem_status]');
  status.value = 'clear'; status.fire('change');
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, []);
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').sample_features, ['模糊']);
  tick(h, '[data-problem-types]', '日期读错');
  assert.equal(status.value, 'issues');
  status.value = 'unmarked'; status.fire('change');
  assert.equal(collectSampleAnnotations(h.ui, {}, 'r').problem_status, 'unmarked');
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, []);
  renderSampleAnnotations(h.ui, h.reviewState, problemTruth(), {});
  assert.equal(status.listenerCount('change'), 1);
  assert.equal(h.$('[data-problem-types]').listenerCount('change'), 1);
});

test('old unscoped labels and unknown saved labels are safely displayed without attributing legacy errors to a test', () => {
  const h = harness(), old = '<旧标签"&>', custom = '自定义"<漏字>&';
  renderSampleAnnotations(h.ui, h.reviewState, problemTruth({legacy_problem_types: [old],
    problem_review: {status: 'issues', types: [custom]}, sample_features: ['自定义图片特征'],
  }), {});
  assert.equal(h.$('[data-problem-legacy]').hidden, true);
  assert.match(h.$('[data-problem-legacy]').textContent, /尚未关联测试/);
  assert(h.$('[data-problem-legacy]').textContent.includes(old));
  assert(!h.$('[data-problem-types]').innerHTML.includes(old));
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, [custom]);
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').sample_features, ['自定义图片特征']);
  tick(h, '[data-problem-types]', old);
  assert.deepEqual(new Set(collectSampleAnnotations(h.ui, {}, 'r').problem_types), new Set([custom, old]));
  renderSampleAnnotations(h.ui, h.reviewState, problemTruth({legacy_problem_types: [old]}), {});
  assert.equal(h.$('[data-problem-legacy]').hidden, false);
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, []);
});

test('a fresh test shows unmarked status while preserving sample note and features', () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, problemTruth({
    problem_review: {status: 'issues', types: ['日期读错']}, sample_features: ['版式变化'],
  }), {});
  renderSampleAnnotations(h.ui, h.reviewState, problemTruth({sample_features: ['版式变化'],
    problem_context: {result_id: 2, task_name: '新一轮测试'},
  }), {});
  const payload = collectSampleAnnotations(h.ui, {}, 'r');
  assert.equal(payload.problem_status, 'unmarked');
  assert.deepEqual(payload.problem_types, []);
  assert.deepEqual(payload.sample_features, ['版式变化']);
  assert.equal(payload.human_note, '已有备注');
  assert.match(h.$('[data-problem-context]').textContent, /新一轮测试/);
});

test('issues without a selected type cannot be saved', async () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, problemTruth(), {});
  h.$('[name=problem_status]').value = 'issues';
  await h.save();
  assert.equal(h.calls.length, 0);
  assert.match(h.notices[0], /请选择至少一个/);
  assert.equal(h.reviewState.reviewSaving, false);
});

test('history summaries show problem state and tags for each result and image feature changes', () => {
  const changes = problemHistoryChanges({sample_features: ['模糊'], problem_reviews: {
    '1': {status: 'clear', types: []},
  }}, {sample_features: ['模糊', '遮挡'], problem_reviews: {
    '1': {status: 'issues', types: ['日期读错', '印章判定错误'], task_name: '第一轮测试'},
  }});
  assert.equal(changes.length, 2);
  assert.match(changes[0], /图片与版式特征: 模糊 → 模糊、遮挡/);
  assert.match(changes[1], /第一轮测试.*已核对无问题 → 已发现问题 · 日期读错 · 印章判定错误/);
  assert.deepEqual(problemHistoryChanges({sample_features: ['模糊', '遮挡']}, {sample_features: ['遮挡', '模糊']}), []);
});

const detected = () => [{type: '日期读错', reason: '已保存标注是22日，本次识别为02日'},
  {type: '印章判定错误', reason: '已保存标注应匹配，本次判定不匹配'}];

test('detected problems prefill a new test as an unsaved draft and never infer image features', () => {
  const h = harness(), data = problemTruth({problem_review_saved: false, problem_suggestions: detected(), sample_features: ['遮挡']});
  const before = structuredClone(data);
  renderSampleAnnotations(h.ui, h.reviewState, data, {});
  const payload = collectSampleAnnotations(h.ui, {}, 'r');
  assert.equal(payload.problem_status, 'issues');
  assert.deepEqual(payload.problem_types, ['日期读错', '印章判定错误']);
  assert.deepEqual(payload.sample_features, ['遮挡']);
  assert.match(h.$('[data-problem-prefill-note]').textContent, /根据已保存标注与本次结果预填 2 项，请核对后保存/);
  assert.equal(h.$('[data-problem-prefill-note]').hidden, false);
  assert.equal(h.$('[data-problem-suggest]').hidden, true);
  assert.equal(h.calls.length, 0);
  assert.equal(h.reviewState.reviewDirty, undefined);
  assert.deepEqual(data, before);
  tick(h, '[data-problem-types]', '日期读错', false);
  assert.match(h.$('[data-problem-prefill-note]').textContent, /预填 1 项/);
  assert.equal(h.$('[data-problem-suggest]').hidden, false);
});

test('previously saved clear issues and unmarked states are not overwritten by new suggestions', () => {
  for (const status of ['clear', 'issues', 'unmarked']) {
    const h = harness(), types = status === 'issues' ? ['基础字段错误'] : [];
    renderSampleAnnotations(h.ui, h.reviewState, problemTruth({problem_review_saved: true,
      problem_review: {status, types}, problem_suggestions: detected(),
    }), {});
    const payload = collectSampleAnnotations(h.ui, {}, 'r');
    assert.equal(payload.problem_status, status);
    assert.deepEqual(payload.problem_types, types);
    assert.equal(h.$('[data-problem-prefill-note]').hidden, true);
    assert.equal(h.$('[data-problem-suggest]').hidden, false);
  }
});

test('explicitly bringing in suggestions merges them with manual choices and protects the unsaved draft', () => {
  const h = harness(), data = problemTruth({problem_review_saved: true,
    problem_review: {status: 'issues', types: ['基础字段错误']}, problem_suggestions: detected(), sample_features: ['模糊']});
  renderSampleAnnotations(h.ui, h.reviewState, data, {});
  renderSampleAnnotations(h.ui, h.reviewState, data, {});
  let changes = 0;
  h.$('[data-problem-types]').addEventListener('change', () => changes++);
  h.$('[data-problem-suggest]').fire('click');
  assert.deepEqual(new Set(collectSampleAnnotations(h.ui, {}, 'r').problem_types), new Set(['基础字段错误', '日期读错', '印章判定错误']));
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').sample_features, ['模糊']);
  assert.equal(changes, 1);
  assert.equal(h.$('[data-problem-suggest]').listenerCount('click'), 1);
  assert.equal(h.$('[data-problem-suggest]').hidden, true);
  assert.match(h.$('[data-problem-prefill-note]').textContent, /预填 2 项/);
  assert.equal(h.calls.length, 0);
});

test('saving a deliberately removed suggestion does not reselect it when the saved form rerenders', async () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, problemTruth({problem_review_saved: false,
    problem_suggestions: detected(),
  }), {});
  tick(h, '[data-problem-types]', '日期读错', false);
  const pending = h.save();
  assert.deepEqual(h.calls[0].payload.problem_types, ['印章判定错误']);
  h.complete(problemTruth({revision: 'r2', problem_review_saved: true,
    problem_review: {status: 'issues', types: ['印章判定错误']}, problem_suggestions: detected(),
  }));
  await pending;
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r2').problem_types, ['印章判定错误']);
  assert.equal(h.$('[data-problem-prefill-note]').hidden, true);
  assert.equal(h.$('[data-problem-suggest]').hidden, false);
  assert.equal(h.reviewState.reviewDirty, false);
});

const channelGroups = () => [
  {label: '日期与其他', types: ['日期读错', '原单印章不符']},
  {label: '清瞳印章', types: ['清瞳印章未检出', '清瞳印章判定错误']},
  {label: '本地印章', types: ['本地印章未检出', '本地印章判定错误']},
];
const channelTruth = overrides => problemTruth({problem_options: {
  types: [...channelGroups().flatMap(group => group.types), '印章判定错误'],
  groups: channelGroups(), features: ['遮挡'],
}, problem_result_summary: {seal_status: '匹配', seal_reliable: true}, ...overrides});

test('seal problems render in source groups and serialize their full source names', () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, channelTruth(), {});
  const markup = h.$('[data-problem-types]').innerHTML;
  assert.match(markup, /aria-label="日期与其他"/);
  assert.match(markup, /aria-label="清瞳印章"/);
  assert.match(markup, /aria-label="本地印章"/);
  assert.match(markup, /value="清瞳印章判定错误" aria-label="清瞳印章判定错误"/);
  assert.match(markup, /<span>判定错误<\/span>/);
  assert(!h.$('[data-problem-types]').querySelectorAll('input').some(input => input.value === '印章判定错误'));
  tick(h, '[data-problem-types]', '清瞳印章判定错误');
  tick(h, '[data-problem-types]', '本地印章未检出');
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, ['清瞳印章判定错误', '本地印章未检出']);
  assert.equal(h.$('[data-problem-result-summary]').textContent, '印章综合识别结论：匹配。下方按识别来源记录问题。');
});

test('old seal labels remain in an unassigned group without being attributed to either source', () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, channelTruth({problem_review_saved: true,
    problem_review: {status: 'issues', types: ['印章判定错误']}, legacy_problem_types: ['印章识别错误'],
  }), {});
  const markup = h.$('[data-problem-types]').innerHTML;
  assert.match(markup, /aria-label="未分来源的标记"/);
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, ['印章判定错误']);
  assert(!h.$('[data-problem-types]').querySelectorAll('input:checked').some(input => /清瞳|本地/.test(input.value)));
  tick(h, '[data-problem-types]', '印章识别错误');
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, ['印章判定错误', '印章识别错误']);
});

test('source-specific suggestions expose escaped reasons and update them when selections change', () => {
  const h = harness(), reason = '清瞳模板读成<img src=x onerror="alert(1)">，人工应匹配';
  renderSampleAnnotations(h.ui, h.reviewState, channelTruth({problem_review_saved: false,
    problem_suggestions: [{type: '清瞳印章判定错误', reason}],
  }), {});
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, ['清瞳印章判定错误']);
  const details = h.$('[data-problem-reasons]');
  assert.equal(details.hidden, false);
  assert.match(details.innerHTML, /清瞳印章判定错误/);
  assert.match(details.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert(!details.innerHTML.includes('<img'));
  tick(h, '[data-problem-types]', '清瞳印章判定错误', false);
  assert.equal(details.hidden, true);
  assert.equal(details.innerHTML, '');
  h.$('[data-problem-suggest]').fire('click');
  assert.equal(details.hidden, false);
  h.$('[name=problem_status]').value = 'clear'; h.$('[name=problem_status]').fire('change');
  assert.equal(details.hidden, true);
  assert.equal(h.$('[data-problem-result-summary]').hidden, false);
});

test('source-specific suggestions cannot overwrite saved selections and a missing current verdict stays hidden', () => {
  const h = harness(); renderSampleAnnotations(h.ui, h.reviewState, channelTruth({problem_review_saved: true,
    problem_review: {status: 'issues', types: ['本地印章判定错误']},
    problem_suggestions: [{type: '清瞳印章判定错误', reason: '清瞳与标注不一致'}],
    problem_result_summary: {},
  }), {});
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, ['本地印章判定错误']);
  assert.equal(h.$('[data-problem-reasons]').hidden, true);
  assert.equal(h.$('[data-problem-result-summary]').hidden, true);
  h.$('[data-problem-suggest]').fire('click');
  assert.deepEqual(collectSampleAnnotations(h.ui, {}, 'r').problem_types, ['清瞳印章判定错误', '本地印章判定错误']);
  assert.match(h.$('[data-problem-reasons]').innerHTML, /清瞳与标注不一致/);
});
