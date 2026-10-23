const test = require('node:test');
const assert = require('node:assert/strict');
const {simplifySealText} = require('../static/modules/core/seal_script.mjs');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');
const {sealEvidenceMarkup} = require('../static/modules/review/review_provider_evidence.mjs');

test('seal display simplifies glyphs without correcting a typo or number', () => {
  assert.equal(simplifySealText('深圳歐瑞特收貨專用章（1）'), '深圳欧瑞特收货专用章（1）');
  assert.equal(simplifySealText('松雄電子①壹A'), '松雄电子①壹A');
});

test('company arc card shows simplified text and retains the original OCR for inspection', () => {
  const target = {innerHTML: ''};
  const raw = '深圳歐瑞特供应链管理有限公司北京分公司';
  const artifact = {schema_version: 2, index: 0, color: 'red', shape: '圆形',
    multi_zone_layout: {method: 'ellipse_continuous_name'},
    inputs: [{id: 'company', channel: 'ring', zone_name: 'company',
      label: '环形公司名称', image_url: '/files/artifacts/test/company.png'}],
    reads: [{input_id: 'company', channel: 'ring', zone_label: '环形公司名称',
      text: raw, confidence: .99}]};
  createReviewEvidence({environment: {}, ui: {$: () => target}, reviewState: {
    artifactTab: 'seals', current: {processing_artifacts: {seals: [artifact]}}
  }}).renderArtifacts();
  assert.match(target.innerHTML, /识别：深圳欧瑞特供应链管理有限公司北京分公司（已转简体）/);
  assert.match(target.innerHTML, /title="原始 OCR：深圳歐瑞特/);
  assert.doesNotMatch(target.innerHTML, /上弧名称|下弧文字/);
  assert.equal(artifact.reads[0].text, raw);
});

test('local provider displays its chosen reading in simplified script', () => {
  const check = {backend: '本地 PaddleOCR PP-OCRv6 Small',
    recognized: '深圳歐瑞特收貨專用章(1)', status: '匹配', reliable: true};
  const markup = sealEvidenceMarkup({seal_check: check});
  assert.match(markup, /深圳欧瑞特收货专用章\(1\)/);
  assert.equal(check.recognized, '深圳歐瑞特收貨專用章(1)');
});
