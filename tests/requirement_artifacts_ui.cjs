const test = require('node:test');
const assert = require('node:assert/strict');
const {createReviewEvidence} = require('../static/modules/review_evidence.mjs');

function render(seals, tab = 'seals', requirement = true) {
  const target = {innerHTML: ''};
  const reviewState = {artifactTab: tab, current: {processing_artifacts: {
    seals, date: [], signature_requirement: requirement ? [{
      original_url: '/files/artifacts/test/requirements/original.png',
      color_clean_url: '/files/artifacts/test/requirements/clean.jpg',
      backend: 'paddle_v6', ocr_text: '签章要求：客户业务章<3>',
    }] : [],
  }}};
  createReviewEvidence({environment: {}, ui: {$: () => target}, reviewState}).renderArtifacts();
  return target.innerHTML;
}

test('requirement input images appear in their own tab and escape OCR text', () => {
  const html = render([], 'signature_requirement');
  assert.match(html, /签章要求行原图/);
  assert.match(html, /签章要求行去印章色图（识别输入）/);
  assert.match(html, /客户业务章&lt;3&gt;/);
  assert.doesNotMatch(html, /没有保存/);
});

test('seal tab does not include requirement images', () => {
  for (const seal of [{index: 0, color: 'red'}, {index: 0, seal_model_backend: 'test'}]) {
    const html = render([seal]);
    assert.doesNotMatch(html, /签章要求行原图/);
  }
});

test('date tab and old records do not show fabricated requirement screenshots', () => {
  assert.doesNotMatch(render([], 'date'), /签章要求行原图/);
  assert.match(render([], 'seals', false), /旧记录可重新识别生成/);
});
