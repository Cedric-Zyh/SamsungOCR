const assert = require('node:assert/strict');
const test = require('node:test');
const {sealEvidenceMarkup} = require('../static/modules/review/review_provider_evidence.mjs');

test('local station match keeps its field explanation visible under an aggregate result', () => {
  const markup = sealEvidenceMarkup({sample_center: true,
    seal_check: {status: '需人工复核', message: '尚无标准章'},
    recognition_variants: {seal: [{method: 'paddle_v6', details: {seal_check: {
      recognized: '三星电子服务中心站代码上海信威6237106TEL:021-51035898',
      status: '匹配', reliable: true,
      field_comparison: {message: '服务中心名称、站代码6237106一致；要求“上海信”，印章“上海信威”'},
    }}}]},
  });
  assert.match(markup, /pill success">匹配/);
  assert.match(markup, /服务中心名称、站代码6237106一致/);
  assert.match(markup, /上海信威/);
  assert.match(markup, /尚无标准章/);
});

test('field notes are escaped as text', () => {
  const markup = sealEvidenceMarkup({seal_check: {
    backend: '本地 PaddleOCR PP-OCRv6 Small', recognized: '读数', status: '匹配',
    field_comparison: {message: '<script>notice</script>'},
  }});
  assert.doesNotMatch(markup, /<script>/);
  assert.match(markup, /&lt;script&gt;/);
});
