const test = require('node:test');
const assert = require('node:assert/strict');
const {renderReceivingEvidence} = require('../static/modules/review/receiving_evidence.mjs');
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

function sample() {
  const item = {original_url: '/files/artifacts/original.png', color_isolated_url: '/files/artifacts/old-color.png',
    inputs: [{id: 'old', channel: 'body', image_url: '/files/artifacts/old-row.png'}],
    reads: [{input_id: 'old', channel: 'body', text: '上海朱泾DC021DCO', confidence: .99}],
    receiving_form: {managed: true, status: 'adopted', identity_text: '上海朱泾DC021DC0收货专用章①',
      selected_source: 'primary', reason: 'usable_primary_retained',
      primary: {form_rows: [{row_index: 2}], conflicts: []},
      grid: {accepted: true, whole_color_url: '/files/artifacts/grid.png', whole_gray_url: '/files/artifacts/grid-gray.png',
        directions: [{direction: 0, decision: {form_rows: [{row_index: 3}]}}, {direction: 180, decision: {form_rows: []}}]},
      split: {accepted: true, title_text: '上海朱泾DC021DC0', identity_text: '上海朱泾DC021DC0收货专用章①',
        requires_review: true, matching_allowed: false,
        conflicts: [{text: '上海朱泾DC021DCO', confidence: .99}]}}};
  for (const [id, channel, index, text, extra] of [
    ['identity', 'receiving_primary', 0, '上海朱泾DC021DC0', {}],
    ['filled', 'receiving_primary', 2, '收货人张三', {}],
    ['detect', 'receiving_detection', 0, '上海朱泾DC021DC0', {}],
    ['grid-id', 'receiving_grid', 2, '收货专用章①', {representation: 'color', receiving_direction: 0}],
    ['grid-field', 'receiving_grid', 3, '日期2026.1.30', {representation: 'gray', receiving_direction: 0}],
    ['opposite', 'receiving_grid', 3, '倒向文字', {representation: 'color', receiving_direction: 180}],
  ]) {
    item.inputs.push({id, channel, rectangle_row_index: index, image_url: `/files/artifacts/${id}.png`, ...extra});
    item.reads.push({input_id: id, channel, text, confidence: .98});
  }
  for (const representation of ['color', 'gray']) {
    for (const [part, scale] of [['left', 1], ['right', 1], ['right', 2]]) {
      const id = `${representation}-${part}-${scale}`;
      item.inputs.push({id, channel: 'receiving_split', representation, split_part: part, split_scale: scale,
        rectangle_row_index: 0, image_url: `/files/artifacts/${id}.png`, matching: false});
      item.reads.push({input_id: id, channel: 'receiving_split', text: part === 'left' ? '上海朱泾DC' : '021DC0', confidence: .97});
    }
  }
  return item;
}

test('ordinary and malformed artifacts produce no receiving UI', () => {
  const {calls, options} = harness();
  for (const item of [{}, null, {receiving_form: null}, {receiving_form: []}, {receiving_form: 'debug'}]) {
    assert.equal(renderReceivingEvidence(item, options), '');
  }
  assert.equal(calls.length, 0);
});

test('a selected identity and conflicting split remain separate conclusions', () => {
  const {options} = harness();
  const html = renderReceivingEvidence(sample(), options);
  assert.match(html, /收货章身份与填写栏/);
  assert.match(html, /已采用收货章身份文字/);
  assert.match(html, /采用的身份文字/);
  assert.match(html, /分段补读存在分歧，需人工核对/);
  assert.match(html, /分段候选（未自动采用）/);
  assert.match(html, /上海朱泾DC021DCO/);
  assert.doesNotMatch(html, /已自动匹配|分段.*已通过|身份读数冲突，需人工核对/);
});

test('accepted requirement code policy is explained while the raw O reading remains visible', () => {
  const {options} = harness();
  const item = sample();
  item.receiving_form.requirement_code_resolution = {accepted: true, policy: 'warehouse_code_zero_o_from_requirement'};
  let html = renderReceivingEvidence(item, options);
  assert.match(html, /0\/O 已按签章要求判定/);
  assert.match(html, /上海朱泾DC021DCO · 99%/);
  item.receiving_form.requirement_code_resolution.accepted = false;
  html = renderReceivingEvidence(item, options);
  assert.doesNotMatch(html, /0\/O 已按签章要求判定/);
});

test('a verified title correction explains the adopted reread without hiding original readings', () => {
  const {calls, options} = harness();
  const item = sample(), form = item.receiving_form;
  const before = '北京永乐bC 010DCM', after = '北京永乐DC 010DCM';
  Object.assign(form, {identity_text: after + '收货专用章②', candidate_text: after + '收货专用章②',
    selected_source: 'verified_title_reread', reason: 'same_row_title_corrected_by_verified_rereads',
    title_read_resolution: {policy: 'same_row_title_reread_consensus', accepted: true,
      row_index: 0, before_texts: [before], text: after},
    split: {accepted: true, requires_review: true, matching_allowed: false,
      title_text: after, identity_text: after + '收货专用章', conflicts: [{text: before, confidence: .98}]}});
  form.primary.conflicts = [{row_index: 0, observations: [
    {text: before, confidence: .975}, {text: after, confidence: .98}]}];
  const html = renderReceivingEvidence(item, options);
  assert.match(html, /首行补读（已采用）/);
  assert.match(html, /第1格原始读数（已采用补读结果）/);
  assert.match(html, /北京永乐bC 010DCM · 97.5%/);
  assert.match(html, /同一处首行字母分歧已由同格补读确认/);
  assert.match(html, /收货专用章②/);
  assert.doesNotMatch(html, /分段补读存在分歧，需人工核对|待核对候选/);
  assert.match(calls.find(call => call.url.endsWith('/detect.png')).label, /同格文字区域重新识别/);

  form.split.conflicts.push({text: '北京永乐DC 011DCM', confidence: .99});
  assert.match(renderReceivingEvidence(item, options), /分段补读存在分歧，需人工核对/);
  form.title_read_resolution.accepted = false;
  const rejected = renderReceivingEvidence(item, options);
  assert.doesNotMatch(rejected, /首行补读（已采用）|第1格原始读数（已采用补读结果）/);
  assert.doesNotMatch(rejected, /已采用补读结果纠正首行字母误读/);
  assert.match(rejected, /第1格的不同读数/);
});

test('a retained same-row consensus is explained while the older extra character remains visible', () => {
  const {options} = harness();
  const item = sample(), form = item.receiving_form;
  const text = '北京永乐DC 010DCM';
  form.identity_text = text + '收货专用章';
  form.primary.identity_rows = [{row_index: 0, text, reread_resolution: {
    policy: 'same_row_reread_tail_consensus', accepted: true, row_index: 0, text,
    before_texts: [text + '1']}}];
  item.reads[0].text = text + '1';
  const html = renderReceivingEvidence(item, options);
  assert.match(html, /第1格（采用一致补读）/);
  assert.match(html, /原始差异保留在下方/);
  assert.match(html, /北京永乐DC 010DCM1 · 99%/);
  form.primary.identity_rows[0].reread_resolution.accepted = false;
  assert.doesNotMatch(renderReceivingEvidence(item, options), /第1格（采用一致补读）/);
  form.primary.identity_rows[0].reread_resolution.accepted = true;
  form.status = 'conflict';
  assert.doesNotMatch(renderReceivingEvidence(item, options), /第1格（采用一致补读）/);
});

test('a cleaned title dot is explained even when another row remains conflicted', () => {
  const {options} = harness();
  const item = sample();
  Object.assign(item.receiving_form, {status: 'conflict', identity_text: '',
    candidate_text: '北京永乐DC010DCM', requirement_title_punctuation: {
      applied: true, policy: 'warehouse_title_punctuation_from_requirement'}});
  item.reads[0].text = '北京永乐DC·010DCM';
  const html = renderReceivingEvidence(item, options);
  assert.match(html, /首行仓库代码前的多余分隔点已按签章要求剔除/);
  assert.match(html, /身份读数冲突，需人工核对/);
  assert.match(html, /待核对候选/);
  assert.match(html, /北京永乐DC010DCM/);
  assert.match(html, /北京永乐DC·010DCM · 99%/);
  assert.doesNotMatch(html, /已采用收货章身份文字/);
});

test('all six literal split views retain words, confidence and colour labels', () => {
  const {calls, options} = harness();
  const html = renderReceivingEvidence(sample(), options);
  const splitCards = calls.filter(call => /分段/.test(call.label));
  assert.equal(splitCards.length, 6);
  assert.equal(splitCards.filter(call => /仓库名分段/.test(call.label)).length, 2);
  assert.equal(splitCards.filter(call => /完整代码分段/.test(call.label)).length, 4);
  assert.equal(splitCards.filter(call => /色差灰度/.test(call.label)).length, 3);
  assert.equal(splitCards.filter(call => /2倍/.test(call.label)).length, 2);
  assert.ok(splitCards.every(call => call.text.includes('97%')));
  assert.match(html, /分段实际输入与原始读数/);
});

test('spacing alone does not create a conflicting candidate, while 0 and O remain distinct', () => {
  const {options} = harness();
  const item = sample();
  item.receiving_form.candidate_text = '上海朱泾DC 021DC0 收货专用章①';
  assert.doesNotMatch(renderReceivingEvidence(item, options), /待核对候选/);
  item.receiving_form.candidate_text = '上海朱泾DC 021DCO 收货专用章①';
  assert.match(renderReceivingEvidence(item, options), /待核对候选/);
});

test('form roles use their own channel and direction, not a global row index', () => {
  const {calls, options} = harness();
  renderReceivingEvidence(sample(), options);
  const call = id => calls.find(value => value.url === `/files/artifacts/${id}.png`);
  assert.match(call('filled').label, /填写栏（不参与匹配）/);
  assert.doesNotMatch(call('identity').label, /填写栏/);
  assert.doesNotMatch(call('grid-id').label, /填写栏/);
  assert.match(call('grid-field').label, /填写栏（不参与匹配）/);
  assert.doesNotMatch(call('opposite').label, /填写栏/);
  assert.match(call('opposite').label, /180°方向对照/);
});

test('original images and old raw reads remain available under audit details', () => {
  const {options} = harness();
  const html = renderReceivingEvidence(sample(), options);
  assert.match(html, /原图与原识别（保留核对）/);
  for (const image of ['original.png', 'old-color.png', 'old-row.png']) assert.ok(html.includes(image));
  assert.match(html, /原识别读数/);
  assert.match(html, /上海朱泾DC021DCO · 99%/);
});

test('unadopted conflicting and insufficient records are explained without debug paths', () => {
  const {options} = harness();
  const item = sample();
  Object.assign(item.receiving_form, {status: 'conflict', identity_text: '',
    candidate_text: '上海朱泾DC021DC0收货专用章①', reason: '/Users/private/debug.py:44'});
  item.receiving_form.primary.conflicts = [{row_index: 0, observations: [
    {text: '上海朱泾DC021DCO', confidence: .99}, {text: '上海朱泾DC021DC0', confidence: .98}]}];
  let html = renderReceivingEvidence(item, options);
  assert.match(html, /身份读数冲突，需人工核对/);
  assert.match(html, /第1格的不同读数/);
  assert.match(html, /待核对候选/);
  assert.doesNotMatch(html, /\/Users\/private|debug\.py/);
  item.receiving_form.status = 'insufficient';
  html = renderReceivingEvidence(item, options);
  assert.match(html, /身份文字证据不足，需人工核对/);
});

test('all user-controlled words are escaped and every image goes through artifactCard', () => {
  const {calls, options} = harness();
  const item = sample();
  const dangerous = '<img src=x onerror="alert(1)">&';
  item.receiving_form.identity_text = dangerous;
  item.receiving_form.candidate_text = dangerous + 'candidate';
  item.receiving_form.split.identity_text = dangerous + 'split';
  item.receiving_form.split.conflicts = [{text: dangerous, confidence: .95}];
  item.reads.forEach(row => { row.text = dangerous; });
  item.inputs.find(input => input.id === 'identity').image_url = 'javascript:alert(1)';
  item.inputs.find(input => input.id === 'identity').label = '/private/tmp/debug.png';
  const html = renderReceivingEvidence(item, options);
  assert.ok(calls.some(call => call.url === 'javascript:alert(1)'));
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;&amp;/);
  assert.doesNotMatch(html, /<img src=x|javascript:|\/private\/tmp/);
  assert.match(html, /处理图不可用/);
});

test('empty and failed reads remain explicit, with invalid confidence kept readable', () => {
  const {options} = harness();
  const item = sample();
  item.inputs.find(input => input.id === 'identity').status = 'failed';
  item.reads = item.reads.filter(read => read.input_id !== 'detect');
  item.reads.find(read => read.input_id === 'filled').confidence = Number.NaN;
  const html = renderReceivingEvidence(item, options);
  assert.match(html, /本次识别失败/);
  assert.match(html, /未读到文字/);
  assert.match(html, /置信度未记录/);
  assert.doesNotMatch(html, /NaN%/);
});

test('an incomplete split is distinguished from a real reading conflict', () => {
  const {options} = harness();
  const item = sample();
  item.receiving_form.split = {accepted: false, requires_review: true, matching_allowed: false,
    reason: 'no_measured_internal_word_gap', conflicts: []};
  item.inputs = item.inputs.filter(input => input.channel !== 'receiving_split');
  const html = renderReceivingEvidence(item, options);
  assert.match(html, /分段补读未形成完整一致候选，保留原识别结果/);
  assert.doesNotMatch(html, /分段补读存在分歧，需人工核对/);
  assert.match(html, /已采用收货章身份文字/);
});

test('sparse historical arrays and unknown source labels do not expose implementation text', () => {
  const {options} = harness();
  const item = sample();
  item.inputs.push(null, 'path');
  item.reads.push(null, 'debug');
  item.receiving_form.primary.conflicts.push(null);
  item.receiving_form.grid.directions.push(null);
  item.receiving_form.selected_source = 'constructor';
  const html = renderReceivingEvidence(item, options);
  assert.match(html, /补充识别/);
  assert.doesNotMatch(html, /function Object|native code|undefined%/);
});
