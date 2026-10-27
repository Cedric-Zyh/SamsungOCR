const test = require('node:test');
const assert = require('node:assert/strict');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');

test('brand evidence is separate from purpose and curved company text', () => {
  const target = {innerHTML: ''};
  const item = {schema_version: 2, index: 0, color: 'red', shape: '椭圆',
    color_isolated_url: '/files/artifacts/test/source.png',
    ring_input_mask_method: 'verified_horizontal_rows', ring_input_box: [290,499,702,616],
    ring_input_url: '/files/artifacts/test/masked.png',
    round_type_band_url: '/files/artifacts/test/purpose.png', round_type_band_text: '服务专用章',
    horizontal_rows: [
      {kind: 'brand', text: 'SAMSUNG', image_url: '/files/artifacts/test/brand.png'},
      {kind: 'type', text: '服务专用章', image_url: '/files/artifacts/test/purpose.png'},
    ],
    unwrapped_url: '/files/artifacts/test/ring.png', unwrapped_text: '电子客户服务中心',
    reads: [{channel: 'horizontal', provider: 'paddle_v6', text: 'SAMSUNG', confidence: .99997}],
  };
  createReviewEvidence({environment: {}, ui: {$: () => target}, reviewState: {
    artifactTab: 'seals', current: {processing_artifacts: {seals: [item]}},
  }}).renderArtifacts();
  assert.match(target.innerHTML, /章内横排文字（独立识别）/);
  assert.match(target.innerHTML, /识别：SAMSUNG/);
  assert.match(target.innerHTML, /各行检测范围填白笔画/);
  assert.match(target.innerHTML, /章内横排文字 · paddle_v6/);
  assert.match(target.innerHTML, /识别：电子客户服务中心/);
  assert.doesNotMatch(target.innerHTML, /第 3 张图/);
});
