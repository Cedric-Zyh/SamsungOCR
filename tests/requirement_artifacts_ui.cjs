const test = require('node:test');
const assert = require('node:assert/strict');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');

function render(seals, tab = 'seals', requirement = true, scope = '') {
  const target = {innerHTML: ''};
  const reviewState = {artifactTab: tab, current: {processing_artifacts: {
    seals, date: [], signature_requirement: requirement ? [{
      original_url: '/files/artifacts/test/requirements/original.png',
      color_clean_url: '/files/artifacts/test/requirements/clean.jpg',
      backend: 'paddle_v6', ocr_text: '签章要求：客户业务章<3>',
      ocr_scope: scope,
    }] : [],
  }}};
  createReviewEvidence({environment: {}, ui: {$: () => target}, reviewState}).renderArtifacts();
  return target.innerHTML;
}

test('normal ring alternative is separate, escaped, and uses the existing image viewer', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '椭圆',
    unwrapped_url: '/files/artifacts/test/primary.png', unwrapped_text: '原来结果',
    ring_normal_alternative: {image_url: '/files/artifacts/test/alternative.png', text: '备选<文字>'},
  }]);
  assert.match(html, /原来结果/);
  assert.match(html, /仅供核对，不参与匹配/);
  assert.match(html, /备选&lt;文字&gt;/);
  assert.match(html, /href="\/files\/artifacts\/test\/alternative.png\?preview=rgb"/);
  assert.match(html, /src="\/files\/artifacts\/test\/primary.png\?preview=rgb"/);
});

test('requirement input images appear in their own tab and escape OCR text', () => {
  const html = render([], 'signature_requirement');
  assert.match(html, /签章要求行原图/);
  assert.match(html, /签章要求行去印章色图（识别输入）/);
  assert.match(html, /客户业务章&lt;3&gt;/);
  assert.doesNotMatch(html, /没有保存/);
});

test('page requirement evidence identifies the page input and never claims a second read', () => {
  const html = render([], 'signature_requirement', true, 'clean_page');
  assert.match(html, /整页识别结果/);
  assert.match(html, /去印章色整页识别输入（局部）/);
  assert.match(html, /客户业务章&lt;3&gt;/);
  assert.doesNotMatch(html, /二次识别/);
});

test('evidence images load eagerly and open inside the review window', () => {
  const html = render([{index: 0, color: 'red', original_url: '/files/stamp.png'}]);
  assert.match(html, /data-artifact-open/);
  assert.match(html, /loading="eager"/);
  assert.match(html, /data-image-retry/);
  assert.doesNotMatch(html, /target="_blank"|loading="lazy"/);
});

test('only unwrapped evidence requests an RGB display copy, including the enlarged link', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '椭圆',
    original_url: '/files/artifacts/test/original.png',
    unwrapped_url: '/files/artifacts/test/ring.png',
    unwrapped_text: '上海凝鹏通讯科技有限公司',
  }]);
  assert.match(html, /href="\/files\/artifacts\/test\/ring.png\?preview=rgb"/);
  assert.match(html, /src="\/files\/artifacts\/test\/ring.png\?preview=rgb"/);
  assert.match(html, /src="\/files\/artifacts\/test\/original.png"/);
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

test('number stroke repair distinguishes the final seal from unchanged ring evidence', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '圆形',
    color_isolated_oriented_url: '/files/restored.png',
    stamp_number_url: '/files/number.png', stamp_number_text: '(1)', stamp_number_status: 'merged',
    ring_input_url: '/files/ring-input.png', ring_input_box: [10, 20, 30, 40],
    stamp_number_recovery: {status: 'restored', original_number_url: '/files/number-original.png'},
  }]);
  assert.match(html, /旋正后的章色图（已保留编号笔画）/);
  assert.match(html, /环形输入仍使用编号修复前的章色图/);
  assert.match(html, /编号原图笔画核对/);
  assert.match(html, /\/files\/number-original.png/);
  assert.match(html, /识别：(.*?)\(1\)/s);
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

test('observed ellipse border is inspectable beside the actual unwrapped input', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '椭圆',
    original_url: '/files/artifacts/test/original.png',
    color_isolated_oriented_url: '/files/artifacts/test/oriented.png',
    ring_input_url: '/files/artifacts/test/ring-input.png',
    unwrapped_url: '/files/artifacts/test/ring.png',
    ring_geometry: 'ellipse_observed_border',
    ring_border_url: '/files/artifacts/test/border.png',
  }]);
  assert.match(html, /沿检测到的边框环展开/);
  assert.match(html, /绿色实测，黄色补全/);
  assert.match(html, /\/files\/artifacts\/test\/border.png/);
});

test('company-only ring input is distinguished from the complete number band', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '圆形',
    original_url: '/files/test/original.png', ring_input_url: '/files/test/ring-input.png',
    unwrapped_url: '/files/test/ring-company.png', unwrapped_full_url: '/files/test/ring.png',
    ring_company_selection: {method: 'small_outer_number_band'},
  }]);
  assert.match(html, /公司名称输入，已排除小字号段/);
  assert.match(html, /完整环形展开图（含小字号段，仅供核对）/);
  assert.match(html, /环形 OCR 读取已排除小字号段的公司名称展开图/);
  assert.doesNotMatch(html, /环形 OCR 直接读取旋正后的整章图/);
});

test('ring serial has an independent preview and read label', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '圆形',
    original_url: '/files/test/original.png', ring_input_url: '/files/test/ring-input.png',
    ring_serial_url: '/files/test/serial-180.png', ring_serial_text: '211402001045714',
    reads: [{channel: 'serial', provider: 'paddle_v6', text: '211402001045714', confidence: .99}],
  }]);
  assert.match(html, /环形编号（独立识别）/);
  assert.match(html, /211402001045714/);
  assert.match(html, /serial-180.png/);
  assert.match(html, /环形编号.*paddle_v6/);
});

test('number evidence shows its own crop and raw text without attributing it to the type-only image', () => {
  const item = {
    schema_version: 2, index: 0, color: 'red', shape: '圆形',
    original_url: '/files/original.png', round_type_band_url: '/files/center.png',
    round_type_row_text: '收货专用章', round_type_band_text: '收货专用章(1',
    stamp_number_url: '/files/number.png', stamp_number_text: '(1', stamp_number_status: 'merged',
    reads: [{channel: 'number', provider: 'paddle_v6', text: '(1', confidence: .91}],
  };
  const html = render([item]);
  const figures = [...html.matchAll(/<figure\b[^>]*>[\s\S]*?<\/figure>/g)].map(m => m[0]);
  const center = figures.find(f => f.includes('/files/center.png'));
  const number = figures.find(f => f.includes('/files/number.png'));
  assert.match(center, /识别：收货专用章<\/p>/);
  assert.doesNotMatch(center, /\(1/);
  assert.match(number, /章内编号（已并入章型文字）/);
  assert.match(number, /识别：\(1<\/p>/);
  assert.match(html, /章内编号 · paddle_v6/);
  assert.match(render([{...item, stamp_number_status: 'unverified'}]), /章内编号（待核对，未合并）/);
});

test('tight text mask explains the actual fill before ring OCR even with company selection', () => {
  const html = render([{
    schema_version: 2, index: 0, color: 'red', shape: '圆形',
    color_isolated_oriented_url: '/files/oriented.png', round_type_band_url: '/files/center.png',
    ring_input_url: '/files/ring-input.png', ring_input_box: [40, 60, 90, 80],
    ring_input_mask_method: 'contained_text_ink', ring_company_selection: {selected: true},
    unwrapped_url: '/files/ring.png',
  }]);
  assert.match(html, /仅填白文字框内的独立笔画/);
  assert.match(html, /保留跨边界的外圈和相邻文字/);
  assert.match(html, /再从第 3 张图展开读取环字/);
  assert.match(html, /文字定位范围/);
  assert.doesNotMatch(html, /dt_polys 合并/);
});
