const test = require('node:test');
const assert = require('node:assert/strict');
const {receivingObservedComponents, renderReceivingObservedComponents} = require('../static/modules/review/receiving_components.mjs');
const {renderReceivingEvidence} = require('../static/modules/review/receiving_evidence.mjs');
const {escapeHtml} = require('../static/modules/core/ui.mjs');

const observation = (text, extra = {}) => ({text, confidence: .94, ...extra});
function artifact(values = [observation('收费专用章②'), observation('收费专用章'), observation('收货专用章')]) {
  return {matching_texts: [], receiving_form: {
    managed: true, status: 'conflict', identity_text: '', candidate_text: '北京永乐DC010DCM',
    primary: {purpose_row_index: 1, identity_rows: [],
      conflicts: [{row_index: 1, observations: values}]},
  }};
}

test('a known number survives a conflicting purpose row without becoming a matching identity', () => {
  const item = artifact();
  const before = structuredClone(item);
  assert.deepEqual(receivingObservedComponents(item), {
    rowIndex: 1, numbers: ['②'], numberStatus: 'observed',
    purposeTexts: ['收费专用章', '收货专用章'], purposeConflict: true,
  });
  const html = renderReceivingObservedComponents(item, {escapeHtml});
  assert.match(html, /已识别编号<\/b><span>②/);
  assert.match(html, /用途文字待核对<\/b><span>收费专用章 \/ 收货专用章/);
  assert.doesNotMatch(html, /94%|已匹配|已确认|北京永乐.*②/);
  assert.deepEqual(item, before);
});

test('the receiving evidence summary displays the separate number and purpose conflict', () => {
  const html = renderReceivingEvidence(artifact(), {escapeHtml, artifactCard: () => ''});
  assert.match(html, /身份读数冲突，需人工核对/);
  assert.match(html, /已识别编号<\/b><span>②/);
  assert.match(html, /用途文字待核对/);
  assert.match(html, /待核对候选<\/b><span>北京永乐DC010DCM/);
  assert.doesNotMatch(html, /已采用收货章身份文字/);
});

test('missing number and duplicated observations are not conflicting number evidence', () => {
  const item = artifact([observation('收货专用章'), observation('收货专用章②')]);
  item.receiving_form.primary.conflicts[0].pairs = [{a: observation('收货专用章②'), b: observation('收货专用章')}];
  const result = receivingObservedComponents(item);
  assert.deepEqual(result.numbers, ['②']);
  assert.equal(result.numberStatus, 'observed');
  assert.equal(result.purposeConflict, false);
  assert.doesNotMatch(renderReceivingObservedComponents(item, {escapeHtml}), /用途文字待核对|编号待核对/);
});

test('different observed circle numbers remain separate conflicts', () => {
  const item = artifact([observation('收货专用章②'), observation('收货专用章③')]);
  assert.deepEqual(receivingObservedComponents(item).numbers, ['②', '③']);
  assert.equal(receivingObservedComponents(item).numberStatus, 'conflict');
  assert.match(renderReceivingObservedComponents(item, {escapeHtml}), /编号待核对<\/b><span>② \/ ③/);
  assert.doesNotMatch(renderReceivingObservedComponents(item, {escapeHtml}), /已识别编号/);
});

test('parenthesized numbers retain their actual glyphs and never become circled numbers', () => {
  const item = artifact([observation('收货专用章 ( 2 )'), observation('收货专用章②'), observation('收货专用章（２）')]);
  assert.deepEqual(receivingObservedComponents(item).numbers, ['(2)', '②', '（２）']);
  assert.equal(receivingObservedComponents(item).numberStatus, 'conflict');
});

test('only the explicitly located purpose row supplies components', () => {
  const item = artifact([observation('收货专用章')]);
  item.receiving_form.primary.conflicts.push({row_index: 2, observations: [observation('收货专用章③')]});
  item.receiving_form.primary.identity_rows.push({row_index: 0, observations: [observation('收货专用章④')]});
  item.receiving_form.grid = {directions: [{direction: 180, rows: [{row_index: 1, text: '收货专用章⑤', confidence: .99}]}]};
  item.reads = [observation('收货专用章⑥', {rectangle_row_index: 1})];
  item.receiving_number = {number_text: '⑦', status: 'adopted'};
  item.fields = {'签章要求': '收货专用章⑧'};
  assert.equal(receivingObservedComponents(item), null);
});

test('another stamp cannot supply a number and the requirement cannot fill it', () => {
  const without = artifact([observation('收货专用章')]);
  const withNumber = artifact([observation('收货专用章②')]);
  without.fields = {'签章要求': '收货专用章②'};
  assert.equal(receivingObservedComponents(without), null);
  assert.deepEqual(receivingObservedComponents(withNumber).numbers, ['②']);
});

test('an adopted identity can show its actual numbered purpose observation', () => {
  const item = artifact([]);
  item.receiving_form.status = 'adopted';
  item.receiving_form.primary.conflicts = [];
  item.receiving_form.primary.identity_rows = [{row_index: 1,
    observations: [observation('收货专用章（12）')]}];
  assert.deepEqual(receivingObservedComponents(item).numbers, ['（12）']);
});

function resolvedArtifact() {
  const item = artifact(), observations = item.receiving_form.primary.conflicts[0].observations;
  const resolution = {policy: 'observed_receiving_purpose', accepted: true, row_index: 1,
    purpose_text: '收货专用章', number_text: '②', text: '收货专用章②', observations};
  item.receiving_form.primary.identity_rows = [{row_index: 1, text: resolution.text,
    observations, purpose_resolution: resolution}];
  item.receiving_form.primary.conflicts = [];
  item.receiving_form.status = 'adopted';
  item.receiving_form.identity_text = '北京永乐DC010DCM收货专用章②';
  return item;
}

test('an accepted same-row purpose resolution is shown as adopted with raw readings preserved', () => {
  const item = resolvedArtifact(), before = structuredClone(item);
  const result = receivingObservedComponents(item);
  assert.deepEqual(result.purposeTexts, ['收货专用章']);
  assert.equal(result.purposeConflict, false);
  assert.equal(result.purposeAdopted, true);
  const html = renderReceivingObservedComponents(item, {escapeHtml});
  assert.match(html, /用途文字（已采用）<\/b><span>收货专用章/);
  assert.doesNotMatch(html, /用途文字待核对/);
  assert.deepEqual(item, before);
  assert(item.receiving_form.primary.identity_rows[0].observations.some(value => value.text === '收费专用章②'));
});

test('unaccepted or unrelated purpose resolutions cannot hide an actual conflict', () => {
  for (const change of [{accepted: false}, {row_index: 2}, {number_text: '③'},
    {policy: 'other'}, {text: '收货专用章③'}]) {
    const item = resolvedArtifact();
    Object.assign(item.receiving_form.primary.identity_rows[0].purpose_resolution, change);
    const result = receivingObservedComponents(item);
    assert.equal(result.purposeConflict, true);
    assert.match(renderReceivingObservedComponents(item, {escapeHtml}), /用途文字待核对/);
  }
});

test('stale resolution metadata cannot supply a purpose absent from actual observations', () => {
  const item = resolvedArtifact();
  item.receiving_form.primary.identity_rows[0].observations = [observation('收费专用章②')];
  const result = receivingObservedComponents(item);
  assert.deepEqual(result.purposeTexts, ['收费专用章']);
  assert.equal(result.purposeAdopted, undefined);
  assert.doesNotMatch(renderReceivingObservedComponents(item, {escapeHtml}), /用途文字（已采用）/);
});

test('legacy pair observations are supported without selecting one by the requirement', () => {
  const item = artifact([]);
  item.receiving_form.primary.conflicts[0].pairs = [
    {a: observation('收费专用章②'), b: observation('收货专用章')},
  ];
  assert.deepEqual(receivingObservedComponents(item).numbers, ['②']);
  assert.equal(receivingObservedComponents(item).purposeConflict, true);
});

test('low confidence, missing scores, failed and simulated readings cannot supply a number', () => {
  for (const extra of [
    {confidence: .74}, {confidence: null}, {confidence: undefined}, {confidence: NaN},
    {confidence: Infinity}, {confidence: 1.01}, {confidence: true}, {confidence: '0.99'},
    {status: 'failed'}, {status: '识别失败'}, {status: '未执行'},
    {simulated: true}, {error: '识别失败'},
  ]) {
    assert.equal(receivingObservedComponents(artifact([observation('收费专用章②', extra)])), null);
  }
  assert.deepEqual(receivingObservedComponents(artifact([observation('收货专用章②', {confidence: .75})])).numbers, ['②']);
});

test('failed or simulated parents cannot present stale observations as an actual number', () => {
  for (const level of ['artifact', 'form', 'primary', 'row']) {
    for (const extra of [{status: 'failed'}, {simulated: true}]) {
      const item = artifact();
      const parent = level === 'artifact' ? item : level === 'form' ? item.receiving_form
        : level === 'primary' ? item.receiving_form.primary : item.receiving_form.primary.conflicts[0];
      Object.assign(parent, extra);
      assert.equal(receivingObservedComponents(item), null);
    }
  }
});

test('date and unrelated digits in the same row are not stamp-number components', () => {
  for (const value of ['日期2026.2.2', '收货人②', '仓库代码②', '②', '收货专用章2',
    '收货专用章②③', '收货专用章（2)', '北京永乐DC010DCM', '收货专用章②日期2026.2.2']) {
    assert.equal(receivingObservedComponents(artifact([observation(value)])), null);
  }
});

test('malformed or unlocated artifacts produce no components', () => {
  for (const item of [null, {}, {receiving_form: []}, {receiving_form: {managed: false}},
    {receiving_form: {managed: true, primary: {purpose_row_index: 1, conflicts: 'old'}}}]) {
    assert.equal(receivingObservedComponents(item), null);
  }
  for (const index of [-1, null, '1', true, 1.5]) {
    const item = artifact();
    item.receiving_form.primary.purpose_row_index = index;
    assert.equal(receivingObservedComponents(item), null);
  }
  const item = artifact([]);
  item.receiving_form.primary.conflicts[0].pairs = [null, false, {}, {a: null, b: {}}];
  assert.equal(receivingObservedComponents(item), null);
  assert.equal(renderReceivingObservedComponents(artifact()), '');
});

test('purpose text is escaped before rendering', () => {
  const html = renderReceivingObservedComponents(artifact([
    observation('收<b>专用章②'), observation('收货专用章'),
  ]), {escapeHtml});
  assert.match(html, /收&lt;b&gt;专用章/);
  assert.doesNotMatch(html, /收<b>专用章/);
});
