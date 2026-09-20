const assert = require('node:assert/strict');
const test = require('node:test');
const workbench = require('../static/workbench.js');
const {createReviewEvidence} = require('../static/modules/review_evidence.mjs');

const artifacts = ['宽区域', '下方扩展区域', '紧凑区域', '远下方手写日期复核区域', '页面底部手写日期复核区域']
  .map((variant, index) => ({variant, original_url: `/files/date-${index}.jpg`}));

test('legacy date previews expose only the compact region and preserve seals', () => {
  const sources = workbench.imageSources({preview_url: '/files/page.jpg', processing_artifacts: {
    date: artifacts, seals: [{original_url: '/files/seal.jpg'}],
  }});
  assert.deepEqual(sources.map(item => item.url), ['/files/page.jpg', '/files/date-2.jpg', '/files/seal.jpg']);
});

test('legacy date evidence hides every non-compact card', () => {
  const target = {innerHTML: ''};
  const reviewState = {artifactTab: 'date', current: {processing_artifacts: {date: artifacts}}};
  const evidence = createReviewEvidence({environment: {}, ui: {$: () => target}, reviewState});
  evidence.renderArtifacts();
  assert.match(target.innerHTML, /紧凑区域/);
  assert.match(target.innerHTML, /date-2.jpg/);
  for (const index of [0, 1, 3, 4]) assert.ok(!target.innerHTML.includes(`date-${index}.jpg`));
  reviewState.current.processing_artifacts.date = [artifacts[0]];
  evidence.renderArtifacts();
  assert.match(target.innerHTML, /没有保存的中间处理图/);
});
