const assert = require('node:assert/strict');
const test = require('node:test');
const {renderSampleAnnotations, collectSampleAnnotations, createSampleAnnotationSubmission} = require('../static/modules/review/sample_annotations.mjs');
const {collectReviewPayload} = require('../static/modules/review/submission.mjs');

function harness() {
  const nodes = new Map();
  const $ = selector => {
    if (!nodes.has(selector)) {
      const classes = new Set();
      nodes.set(selector, {value: '', textContent: '', disabled: false,
        classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)}});
    }
    return nodes.get(selector);
  };
  const notices = [], calls = [], controls = [$('[name=human_note]'), $('[data-save-sample]'), $('[name=machine-evidence]')];
  controls[2].disabled = true;
  const ui = {$, $$: () => controls, toast: message => notices.push(message)};
  const reviewState = {sampleView: true, reviewSaving: false, current: {id: 1, fields: {'客户名称': '机器客户'}}};
  let complete, fail, refreshes = 0, histories = 0;
  const save = createSampleAnnotationSubmission({ui, reviewState, services: {samples: {saveAnnotations: (id, payload) => {
    calls.push({id, payload}); return new Promise((resolve, reject) => {complete = resolve; fail = reject;});
  }}}, renderHistory: async () => histories++, refreshVisibleResults: async () => refreshes++});
  return {ui, $, reviewState, save, notices, calls, controls, complete: value => complete(value), fail: error => fail(error), counts: () => ({refreshes, histories})};
}

const truth = () => ({revision: 'revision-1', entry: {fields: {'客户名称': '人工客户', '签章要求': '人工签章要求', '要求到货': '2026-03-27'},
  date_present: true, actual_date: '2026-03-27', seal_should_match: false, human_note: '已有备注', error_type: '印章识别错误'}});

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
