const test = require('node:test');
const assert = require('node:assert/strict');
const {renderReceivingNumberEvidence} = require('../static/modules/review/receiving_number_evidence.mjs');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');
const {escapeHtml} = require('../static/modules/core/ui.mjs');

function harness() {
  const calls = [];
  const artifactCard = (label, url, text = '', rgb = false) => {
    calls.push({label, url, text, rgb});
    if (typeof url !== 'string' || !url.startsWith('/files/')) return '';
    return `<figure data-image="${escapeHtml(url)}"><b>${escapeHtml(label)}</b><span>${escapeHtml(text)}</span></figure>`;
  };
  return {calls, options: {artifactCard, escapeHtml}};
}

function fixture(status = 'adopted') {
  return {
    receiving_number: {status, row_index: 1, number_text: '②',
      original_purpose_texts: ['收货专用章'], combined_purpose_text: '收货专用章②',
      reason: 'same_row_two_color_reads_agree', inputs: [
        {id: 'number-1', channel: 'receiving_number', number_scale: 1, representation: 'color', image_url: '/files/artifacts/test/native.png'},
        {id: 'number-2', channel: 'receiving_number', number_scale: 2, representation: 'color', image_url: '/files/artifacts/test/double.png'},
      ]},
    reads: [
      {input_id: 'number-1', channel: 'receiving_number', text: '②', confidence: .87, matching: false},
      {input_id: 'number-2', channel: 'receiving_number', text: '②', confidence: .993, matching: false},
    ],
  };
}

test('adopted circle reread shows both real color inputs and all raw confidence readings', () => {
  const item = fixture();
  const before = JSON.stringify(item);
  const h = harness();
  const html = renderReceivingNumberEvidence(item, h.options);
  assert.match(html, /右侧圈号补读/);
  assert.match(html, /圈号已并入同一用途行/);
  assert.match(html, /并入后的用途文字/);
  assert.match(html, /用途所在格<\/b><span>第2格/);
  assert.match(html, /收货专用章②/);
  assert.match(html, /87%/);
  assert.match(html, /99.3%/);
  assert.deepEqual(h.calls.map(call => [call.url, call.rgb]), [
    ['/files/artifacts/test/native.png', false], ['/files/artifacts/test/double.png', false],
  ]);
  assert.match(h.calls[0].label, /原尺寸 · 原色/);
  assert.match(h.calls[1].label, /2倍 · 原色/);
  assert.equal(JSON.stringify(item), before);
});

test('conflicting and multiple raw readings remain visible and are never described as adopted', () => {
  const item = fixture('conflict');
  item.receiving_number.reason = 'existing_identity_number_or_purpose_conflict';
  item.reads[0].text = '①';
  item.reads.push({input_id: 'number-1', text: '③', confidence: .8, matching: false});
  const html = renderReceivingNumberEvidence(item, harness().options);
  assert.match(html, /未并入用途文字/);
  assert.match(html, /待核对用途候选（未采用）/);
  assert.match(html, /① · 87% \/ ③ · 80%/);
  assert.match(html, /② · 99.3%/);
  assert.doesNotMatch(html, /圈号已并入|并入后的用途文字/);
});

test('failed, empty, unavailable and orphan evidence retains actual readings', () => {
  const item = fixture('unverified');
  item.receiving_number.inputs[0].status = 'failed';
  item.receiving_number.inputs[0].image_url = 'javascript:alert(1)';
  item.receiving_number.inputs[1].image_url = null;
  item.reads[0].text = '③';
  item.reads[0].confidence = 0;
  item.reads[1].text = '';
  item.reads[1].confidence = NaN;
  item.reads.push({input_id: 'missing-image', channel: 'receiving_number', text: '①', confidence: .81});
  const html = renderReceivingNumberEvidence(item, harness().options);
  assert.match(html, /本次识别失败 · ③ · 0%/);
  assert.match(html, /未读到文字 · 置信度未记录/);
  assert.match(html, /处理图不可用/);
  assert.match(html, /未关联补读图的原始读数/);
  assert.match(html, /① · 81%/);
  assert.doesNotMatch(html, /javascript:|NaN%/);
});

test('only friendly reasons are shown and every user-controlled text is escaped', () => {
  const item = fixture('unverified');
  item.receiving_number.reason = 'secret_internal_reason_<script>/private/path';
  item.receiving_number.number_text = '<img src=x onerror=1>';
  item.receiving_number.original_purpose_texts = ['<b>原用途</b>', {text: 'ignored'}];
  item.receiving_number.combined_purpose_text = '<script>candidate</script>';
  item.reads[0].text = '<svg onload=1>';
  const html = renderReceivingNumberEvidence(item, harness().options);
  assert.match(html, /&lt;img src=x onerror=1&gt;/);
  assert.match(html, /&lt;b&gt;原用途&lt;\/b&gt;/);
  assert.match(html, /&lt;script&gt;candidate&lt;\/script&gt;/);
  assert.match(html, /&lt;svg onload=1&gt;/);
  assert.doesNotMatch(html, /secret_internal_reason|private\/path|<script>|<img |<svg |\[object Object\]/);
});

test('not-detected metadata without inputs has a concise explanation', () => {
  const html = renderReceivingNumberEvidence({receiving_number: {status: 'not_detected', reason: 'unknown_debug_code'}}, harness().options);
  assert.match(html, /未检测到可采用的右侧圈号/);
  assert.doesNotMatch(html, /unknown_debug_code|artifact-grid/);
});

test('fallback inputs and malformed fields do not associate unrelated missing-id reads', () => {
  const h = harness();
  const item = {receiving_number: {status: 'unverified', inputs: [null, false], original_purpose_texts: {}},
    inputs: [{id: 'actual', channel: 'receiving_number', number_scale: 1, representation: 'color', image_url: '/files/test.png'},
      {channel: 'receiving_number', image_url: '/files/no-id.png'}],
    reads: [{text: 'unrelated missing id', confidence: .9}, {input_id: 'actual', text: '②', confidence: 1.5}]};
  const html = renderReceivingNumberEvidence(item, h.options);
  assert.equal(h.calls.length, 2);
  assert.match(html, /② · 置信度未记录/);
  assert.match(html, /未读到文字/);
  assert.doesNotMatch(html, /unrelated missing id|150%/);
  for (const value of [undefined, null, false, [], 'metadata', {}]) {
    assert.equal(renderReceivingNumberEvidence({receiving_number: value}, h.options), '');
  }
  assert.equal(renderReceivingNumberEvidence(item), '');
});

test('review page renders right-side number evidence even when receiving form is absent', () => {
  for (const form of [undefined, {status: 'conflict', primary: {}}]) {
    const target = {innerHTML: ''};
    const item = {...fixture(form ? 'unverified' : 'adopted'), schema_version: 2, index: 0, color: 'red', shape: '矩形', receiving_form: form};
    const reviewState = {artifactTab: 'seals', current: {processing_artifacts: {seals: [item]}}};
    createReviewEvidence({environment: {}, ui: {$: () => target}, reviewState}).renderArtifacts();
    assert.match(target.innerHTML, /右侧圈号补读/);
    assert.match(target.innerHTML, /native.png/);
    assert.match(target.innerHTML, /double.png/);
    assert.match(target.innerHTML, form ? /待核对用途候选（未采用）/ : /并入后的用途文字/);
  }
});
