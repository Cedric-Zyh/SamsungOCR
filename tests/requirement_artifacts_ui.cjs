const test = require('node:test');
const assert = require('node:assert/strict');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');

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

test('seal evidence labels the saved white-band ring OCR input', () => {
  const html = render([{
    index: 0,
    color: 'red',
    shape: '圆形',
    original_url: '/files/artifacts/test/seals/original.png',
    color_isolated_oriented_url: '/files/artifacts/test/seals/oriented.png',
    round_type_band_url: '/files/artifacts/test/seals/center.png',
    ring_input_url: '/files/artifacts/test/seals/ring-input.png',
    unwrapped_url: '/files/artifacts/test/seals/ring.png',
  }]);
  assert.match(html, /章型分带白底覆盖图（环形 OCR 实际输入）/);
});

test('modern round seal UI separates the detected band from the full-image masked input', () => {
  const html = render([{
    schema_version: 2,
    index: 0,
    color: 'red',
    shape: '圆形',
    original_url: '/files/artifacts/test/seals/original.png',
    color_isolated_oriented_url: '/files/artifacts/test/seals/oriented.png',
    round_type_band_url: '/files/artifacts/test/seals/center.png',
    ring_input_url: '/files/artifacts/test/seals/ring-input.png',
    ring_input_box: [10, 20, 30, 40],
    unwrapped_url: '/files/artifacts/test/seals/ring.png',
  }]);
  assert.match(html, /旋正后的章色图（未覆盖）/);
  assert.match(html, /检测到的横向文字区域（仅定位\/章型 OCR）/);
  assert.match(html, /旋正章色图 · 横向文字区域填白（环形 OCR 实际输入）/);
  assert.match(html, /只将 OCR dt_polys 合并出的检测框填白/);
  assert.doesNotMatch(html, /章型分带白底覆盖图（环形 OCR 实际输入）/);
});

test('modern round seal UI supports direct ring input without a horizontal type row', () => {
  const html = render([{
    schema_version: 2,
    index: 0,
    color: 'red',
    shape: '圆形',
    original_url: '/files/artifacts/test/seals/original.png',
    color_isolated_oriented_url: '/files/artifacts/test/seals/oriented.png',
    ring_input_url: '/files/artifacts/test/seals/ring-input.png',
    unwrapped_url: '/files/artifacts/test/seals/ring.png',
  }]);
  assert.match(html, /无横向文字，直接作为环形 OCR 实际输入/);
  assert.match(html, /由整章图展开/);
  assert.match(html, /环形 OCR 直接读取旋正后的整章图/);
});

test('seal evidence shows raw ring OCR and its cyclic seam reorder separately', () => {
  const html = render([{
    schema_version: 2,
    index: 0,
    color: 'red',
    shape: '圆形',
    original_url: '/files/artifacts/test/seals/original.png',
    color_isolated_oriented_url: '/files/artifacts/test/seals/oriented.png',
    round_type_band_url: '/files/artifacts/test/seals/center.png',
    ring_input_url: '/files/artifacts/test/seals/ring-input.png',
    unwrapped_url: '/files/artifacts/test/seals/ring.png',
    unwrapped_raw_text: '公司济南新宇航科技发展有限',
    unwrapped_text: '济南新宇航科技发展有限公司',
    ring_reorder: {changed: true, seam_offset: 2, reason: 'company_suffix_cyclic_reorder'},
    reads: [{channel: 'ring', provider: 'paddle_v6', text: '公司济南新宇航科技发展有限'}],
  }]);
  assert.match(html, /环形 OCR 原始/);
  assert.match(html, /环形首尾重排/);
  assert.match(html, /济南新宇航科技发展有限公司/);
});

test('date tab and old records do not show fabricated requirement screenshots', () => {
  assert.doesNotMatch(render([], 'date'), /签章要求行原图/);
  assert.match(render([], 'seals', false), /旧记录可重新识别生成/);
});
