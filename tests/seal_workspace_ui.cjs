const test = require('node:test');
const assert = require('node:assert/strict');
const {sealWorkspaceModel, renderSealWorkspace, sealImageUrl, mergeSealArtifacts,
  preferredSealIndex, bindSealWorkspaces} = require('../static/modules/review/seal_workspace.mjs');
const {sealWorkspaceArtifacts} = require('../static/modules/review/seal_artifacts.mjs');

function fixture() {
  return {original_url: '/files/original.png', color_isolated_oriented_url: '/files/oriented.png',
    receiving_form: {managed: true, status: 'adopted', identity_text: '北京永乐DC 010DCM收货专用章②',
      candidate_text: '北京永乐DC010DCM收货专用章', primary: {purpose_row_index: 1,
        identity_rows: [{row_index: 0, text: '北京永乐DC 010DCM'}, {row_index: 1, text: '收货专用章②'}],
        form_rows: [{row_index: 2, text: '收货人张三'}]}},
    inputs: [
      {channel: 'receiving_primary', rectangle_row_index: 0, image_url: '/files/title.png'},
      {channel: 'receiving_primary', rectangle_row_index: 1, image_url: '/files/purpose.png'},
      {channel: 'receiving_primary', rectangle_row_index: 2, image_url: '/files/filled.png'},
    ]};
}

function multipleSeals() {
  return {processing_artifacts: {seals: [
    {index: 0, color: 'red', original_url: '/files/first-original.png', color_isolated_oriented_url: '/files/first-processed.png',
      rectangular_text: '卜服科技服务有限公司维修中心专用章(04)'},
    {index: 1, color: 'red', original_url: '/files/second-original.png', color_isolated_oriented_url: '/files/second-processed.png',
      rectangular_text: '京小服科技服务有限公司维修中心专用章(04)'},
    {index: 0, color: 'red', source: '清瞳印章区域', api_index: 1, original_url: '/files/remote-first.png'},
    {index: 1, color: 'red', source: '清瞳印章区域', api_index: 2, original_url: '/files/remote-second.png'},
  ]}, seal_check: {recognized: '京小服科技服务有限公司维修中心专用章(04)',
    dual_check: {selected: {index: 1}}}, seal_standard_check: {selected_id: 'second', candidates: [
      {id: 'first', index: 0, original_url: '/files/first-original.png', text_status: '不匹配', score: .923},
      {id: 'second', index: 1, original_url: '/files/second-original.png', image_url: '/files/second-processed.png',
        matched: true, text_status: '匹配', score: 1},
    ]}};
}

test('the saved comparison winner selects the second physical seal despite overlapping provider indexes', () => {
  const record = multipleSeals(), before = structuredClone(record);
  const items = mergeSealArtifacts(record.processing_artifacts.seals);
  assert.equal(items.length, 2);
  const selected = preferredSealIndex(record, items);
  assert.equal(selected, 1);
  assert.equal(sealWorkspaceModel(items[selected]).preferred, '/files/second-processed.png');
  assert.deepEqual(record, before);
});

test('saved comparison scores and selected text retain the closest available stamp without inventing a match', () => {
  const record = multipleSeals(), items = mergeSealArtifacts(record.processing_artifacts.seals);
  delete record.seal_standard_check.selected_id;
  assert.equal(preferredSealIndex(record, items), 1);
  delete record.seal_standard_check;
  record.seal_check = {recognized: '京小服科技服务有限公司维修中心专用章(04)', status: '部分匹配'};
  assert.equal(preferredSealIndex(record, items), 1);
  assert.equal(record.seal_check.status, '部分匹配');
});

test('workspace selection and preview start on the winner while manual selection remains usable', () => {
  const record = multipleSeals(), items = mergeSealArtifacts(record.processing_artifacts.seals), previews = [];
  const panels = items.map(() => ({hidden: false}));
  const buttons = items.map((_, index) => ({dataset: {sealWorkspaceSelect: String(index)},
    setAttribute(name, value) { this[name] = value; }, addEventListener(_type, listener) { this.click = listener; }}));
  const root = {querySelectorAll: selector => selector === '[data-seal-workspace-panel]' ? panels
    : selector === '[data-seal-workspace-select]' ? buttons : []};
  const show = bindSealWorkspaces(root, items, detail => previews.push(detail), record);
  assert.deepEqual(panels.map(panel => panel.hidden), [true, false]);
  assert.deepEqual(buttons.map(button => button['aria-pressed']), ['false', 'true']);
  show();
  assert.equal(previews.at(-1).label, '印章 2');
  assert.equal(previews.at(-1).url, '/files/second-processed.png');
  buttons[0].click();
  assert.deepEqual(panels.map(panel => panel.hidden), [false, true]);
  assert.equal(previews.at(-1).url, '/files/first-processed.png');
});

test('recognition evidence markup starts with the same selected seal as the main preview', () => {
  const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');
  const target = {innerHTML: '', querySelectorAll: () => []};
  createReviewEvidence({environment: {}, ui: {$: () => target},
    reviewState: {artifactTab: 'seals', current: multipleSeals()}}).renderArtifacts();
  assert.match(target.innerHTML, /data-seal-workspace-select="1" aria-pressed="true"/);
  assert.match(target.innerHTML, /data-seal-workspace-panel="0" hidden/);
  assert.match(target.innerHTML, /data-seal-workspace-panel="1">/);
});

function textOnlyRemote() {
  return {sample_center: true, filename: '7303331190.jpg', preview_url: '/files/samples/previews/page.jpg',
    seal_check: {recognized: '本地识别文字', backend: '本地 OCR'}, processing_artifacts: {seals: [
      {index: 0, original_url: '/files/local-crop.png', combined_text: '本地识别文字'},
    ]}, recognition_variants: {seal: [{method: 'danzhengtong', value: ['单证通识别文字'],
      details: {seal_check: {recognized: '单证通识别文字', status: '部分匹配'}, processing_artifacts: {seals: []}}}]}};
}

test('text-only remote results remain separate from local stamps and preview the uploaded sample without rewriting data', () => {
  const record = textOnlyRemote(), before = structuredClone(record), items = sealWorkspaceArtifacts(record);
  assert.equal(items.length, 2);
  assert.equal(sealWorkspaceModel(items[0]).text, '本地识别文字');
  const remote = sealWorkspaceModel(items[1]);
  assert.equal(remote.text, '单证通识别文字');
  assert.equal(remote.status, '部分匹配');
  assert.equal(remote.preferred, '/files/samples/original/7303331190.jpg');
  assert.equal(remote.processed, '');
  assert.equal(remote.originalLabel, '上传图（整页回单）');
  const html = renderSealWorkspace(items[1]);
  assert.match(html, /单证通印章识别结果/);
  assert.match(html, /未保存单证通处理图，当前展示上传图/);
  assert.doesNotMatch(html, /本地识别文字|local-crop|上传的增强图/);
  assert.deepEqual(record, before);
});

test('an artifact-free remote record still renders its saved result and input link instead of the empty-artifacts message', () => {
  const record = textOnlyRemote(); delete record.processing_artifacts;
  const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');
  const target = {innerHTML: '', querySelectorAll: () => []};
  createReviewEvidence({environment: {}, ui: {$: () => target},
    reviewState: {artifactTab: 'seals', current: record}}).renderArtifacts();
  assert.match(target.innerHTML, /单证通印章识别结果/);
  assert.match(target.innerHTML, /单证通识别文字/);
  assert.match(target.innerHTML, /部分匹配/);
  assert.match(target.innerHTML, /data-seal-evidence-url="\/files\/samples\/original\/7303331190.jpg"/);
  assert.doesNotMatch(target.innerHTML, /没有保存的中间处理图|旧记录可重新识别生成/);
});

test('the remote source selector switches to its own text and uploaded image with the same projected panel indexes', () => {
  const record = textOnlyRemote(), items = sealWorkspaceArtifacts(record), previews = [];
  const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');
  const panels = items.map(() => ({hidden: false}));
  const buttons = items.map((_, index) => ({dataset: {sealWorkspaceSelect: String(index)},
    setAttribute(name, value) { this[name] = value; }, addEventListener(_type, listener) { this.click = listener; }}));
  const target = {innerHTML: '', closest: () => ({open: true}), querySelectorAll: selector =>
    selector === '[data-seal-workspace-panel]' ? panels : selector === '[data-seal-workspace-select]' ? buttons : []};
  createReviewEvidence({environment: {}, ui: {$: () => target}, previewSeal: detail => previews.push(detail),
    reviewState: {artifactTab: 'seals', current: record}}).renderArtifacts();
  assert.match(target.innerHTML, /data-seal-workspace-select="1"[^>]*>单证通<\/button>/);
  assert.equal(previews.at(-1).url, '/files/local-crop.png');
  buttons[1].click();
  assert.deepEqual(panels.map(panel => panel.hidden), [true, false]);
  assert.equal(previews.at(-1).label, '单证通');
  assert.equal(previews.at(-1).text, '单证通识别文字');
  assert.equal(previews.at(-1).url, '/files/samples/original/7303331190.jpg');
});

test('provider-specific uploads and returned crops survive projection without creating duplicate remote entries', () => {
  const record = textOnlyRemote();
  const artifact = {provider: 'danzhengtong', index: 0, input_url: '/files/remote-upload.png',
    returned_image_url: '/files/remote-returned.png', returned_image_status: 'downloaded', provider_text: '单证通识别文字'};
  record.processing_artifacts.seals.push(artifact);
  record.recognition_variants.seal[0].details.processing_artifacts.seals.push(artifact);
  let items = sealWorkspaceArtifacts(record);
  assert.equal(items.length, 2);
  assert.equal(sealWorkspaceModel(items[1]).preferred, '/files/remote-returned.png');
  assert.equal(sealWorkspaceModel(items[1]).status, '部分匹配');
  artifact.returned_image_status = 'missing';
  assert.equal(sealWorkspaceModel(sealWorkspaceArtifacts(record)[1]).preferred, '/files/remote-upload.png');
  record.processing_artifacts.seals.pop();
  assert.equal(sealWorkspaceArtifacts(record).length, 2);
});

test('legacy single-source results use uploaded receipt files and unsafe image inputs cannot escape the local preview', () => {
  const record = {stored_name: '上传 回单.jpg', preview_url: '/files/previews/p.jpg',
    seal_check: {source: '单证通', recognized: '<img src=x onerror=bad()>', status: '匹配'}};
  let items = sealWorkspaceArtifacts(record);
  assert.equal(items.length, 1);
  assert.equal(sealWorkspaceModel(items[0]).preferred, '/files/uploads/%E4%B8%8A%E4%BC%A0%20%E5%9B%9E%E5%8D%95.jpg');
  delete record.stored_name; record.preview_url = 'https://example.com/private.png';
  record.seal_check.input_url = 'javascript:bad()';
  items = sealWorkspaceArtifacts(record);
  assert.equal(sealWorkspaceModel(items[0]).preferred, '');
  assert.match(renderSealWorkspace(items[0]), /&lt;img/);
  assert.doesNotMatch(renderSealWorkspace(items[0]), /<img|javascript:|example.com/);
  assert.deepEqual(sealWorkspaceArtifacts(null), []);
  assert.deepEqual(sealWorkspaceArtifacts({recognition_config: {seal: ['danzhengtong']}}), []);
});

test('adopted identity is decomposed without removing the literal circled number or rewriting data', () => {
  const item = fixture(), original = structuredClone(item);
  const model = sealWorkspaceModel(item);
  assert.deepEqual(model.fields.map(field => field.value), ['北京永乐DC 010DCM', '收货专用章', '②']);
  assert.equal(model.otherCandidate, '');
  assert.equal(model.formFields[0].url, '/files/filled.png');
  assert.deepEqual(item, original);
});

test('real code or number differences are retained even when a complete result was adopted', () => {
  for (const candidate of ['北京永乐DC011DCM收货专用章②', '北京永乐DC010DCM收货专用章③']) {
    const item = fixture(); item.receiving_form.candidate_text = candidate;
    assert.equal(sealWorkspaceModel(item).otherCandidate, candidate);
  }
});

test('unadopted candidates never become selected identity and conflicting numbers stay visible', () => {
  const item = fixture(), form = item.receiving_form;
  form.status = 'conflict';
  form.primary.conflicts = [{row_index: 1, observations: [
    {text: '收货专用章②', confidence: .98}, {text: '收货专用章③', confidence: .97},
  ]}];
  const model = sealWorkspaceModel(item);
  assert.equal(model.adopted, false);
  assert.equal(model.selected, '');
  assert.equal(model.fields[2].conflict, '② / ③');
  const html = renderSealWorkspace(item);
  assert.match(html, /② \/ ③/);
  assert.match(html, /读数有分歧/);
  assert.doesNotMatch(html, /is-adopted/);
});

test('field preview cannot use the opposite orientation or another physical row', () => {
  const item = fixture();
  item.receiving_form.selected_source = 'verified_grid_recovery';
  item.inputs.unshift(
    {channel: 'receiving_grid', rectangle_row_index: 0, receiving_direction: 180, image_url: '/files/opposite.png'},
    {channel: 'receiving_grid', rectangle_row_index: 0, receiving_direction: 0, image_url: '/files/grid-title.png'},
  );
  const model = sealWorkspaceModel(item);
  assert.equal(model.fields[0].url, '/files/grid-title.png');
  assert.equal(model.fields[1].url, '/files/purpose.png');
});

test('a separately saved circle crop takes precedence for the number link only', () => {
  const item = fixture();
  item.receiving_number = {inputs: [{image_url: '/files/circle.png'}]};
  assert.deepEqual(sealWorkspaceModel(item).fields.map(row => row.url),
    ['/files/title.png', '/files/purpose.png', '/files/circle.png']);
});

test('recognition process and filled fields start collapsed and preserve their evidence', () => {
  const html = renderSealWorkspace(fixture(), {audit: '<div>旧读数与处理图片</div>'});
  assert.match(html, /<details class="seal-recognition-process"><summary>识别过程/);
  assert.match(html, /<details class="seal-filled-fields"><summary>收货人、日期/);
  assert.match(html, /旧读数与处理图片/);
  assert.doesNotMatch(html, /待核对候选|<img/);
});

test('unknown legacy records remain readable without inventing an adopted result', () => {
  const item = {isolated_text: '旧印章文字'};
  const model = sealWorkspaceModel(item);
  assert.equal(model.status, '识别文字');
  assert.equal(model.adopted, false);
  assert.equal(model.text, '旧印章文字');
  assert.match(renderSealWorkspace(item), /未保存图片/);
  assert.doesNotMatch(renderSealWorkspace(item), /data-seal-evidence-url/);
});

test('evidence urls are local and all recognized text is escaped', () => {
  for (const url of ['javascript:alert(1)', 'https://example.com/a', '/files/../config/key', '/files/\\x', null]) {
    assert.equal(sealImageUrl(url), '');
  }
  const item = fixture();
  item.receiving_form.identity_text = '<img src=x onerror=alert(1)>';
  item.original_url = 'javascript:alert(1)';
  const html = renderSealWorkspace(item);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img|javascript:/);
});

test('legacy receiving rectangles use their saved full text and literal row crops', () => {
  const item = {shape: '矩形', rectangular_layout: 'frame_rows',
    rectangular_text: '北京永乐DC 010DCM收货专用章②',
    rectangular_row_texts: ['北京永乐DC 010DCM', '收货专用章②', '', ''],
    rectangular_row_urls: ['/files/title.png', '/files/purpose.png', '/files/person.png', '/files/date.png'],
    rectangular_form_fields: [{text: '收货人', rectangle_row_index: 2}, {text: '日期', rectangle_row_index: 3}]};
  const before = structuredClone(item), model = sealWorkspaceModel(item);
  assert.equal(model.text, item.rectangular_text);
  assert.deepEqual(model.fields.map(field => [field.label, field.value, field.url]), [
    ['仓库与代码', '北京永乐DC 010DCM', '/files/title.png'],
    ['印章用途', '收货专用章', '/files/purpose.png'], ['印章编号', '②', '/files/purpose.png']]);
  assert.equal(model.formFields.length, 2);
  assert.doesNotMatch(renderSealWorkspace(item), /尚无可采用的完整结果/);
  assert.deepEqual(item, before);
});

test('round seals expose company, purpose and their actual parenthesized number', () => {
  const item = {shape: '圆形', combined_text: '测试供应链有限公司收货专用章(1)',
    unwrapped_text: '测试供应链有限公司', unwrapped_url: '/files/samples/artifacts/task/company.png',
    round_type_band_text: '收货专用章', round_type_band_url: '/files/purpose.png',
    stamp_number_text: '(1)', stamp_number_url: '/files/number.png'};
  const model = sealWorkspaceModel(item);
  assert.deepEqual(model.fields.map(field => [field.label, field.value]), [
    ['单位名称', '测试供应链有限公司'], ['印章用途', '收货专用章'], ['印章编号', '(1)']]);
  assert.equal(model.fields[0].url, '/files/samples/artifacts/task/company.png?preview=rgb');
  assert.equal(model.fields[2].url, '/files/number.png');
  assert.doesNotMatch(renderSealWorkspace(item), /①/);
});

test('ellipse fields keep department, station code, phone and website separate', () => {
  const model = sealWorkspaceModel({shape: '椭圆', unwrapped_text: '测试服务公司',
    department_text: '仓储部', department_url: '/files/department.png',
    round_type_band_text: '维修专用章', round_type_band_url: '/files/type.png',
    station_code_text: '站代码：5988247', phone_text: '02081769324', phone_url: '/files/phone.png',
    website_text: 'www.example.com', website_url: '/files/site.png'});
  assert.deepEqual(model.fields.map(field => field.label), ['单位名称', '章内部门', '印章用途', '站点代码', '联系电话', '网址']);
  assert.equal(model.fields.find(field => field.label === '联系电话').value, '02081769324');
});

test('replaced reads cannot supply company text or a crop; active input links survive missing summary properties', () => {
  const item = {shape: '圆形', inputs: [
    {id: 'old', zone_name: 'company', superseded_by: 'new', image_url: '/files/old.png'},
    {id: 'new', zone_name: 'company', channel: 'ring', image_url: '/files/artifacts/t/new.png'},
  ], reads: [
    {input_id: 'old', channel: 'ring', zone_label: '环形公司名称', text: '错误公司'},
    {input_id: 'new', channel: 'ring', zone_label: '环形公司名称', text: '实际公司'},
  ]};
  const model = sealWorkspaceModel(item);
  assert.equal(model.status, '分项结果');
  assert.equal(model.fields.length, 1);
  assert.equal(model.fields[0].value, '实际公司');
  assert.equal(model.fields[0].url, '/files/artifacts/t/new.png?preview=rgb');
  assert.doesNotMatch(renderSealWorkspace(item), /错误公司|尚无可采用的完整结果/);
});

test('unclassified fields and conflicting reads stay visible without synthesizing an identity', () => {
  const item = {shape: '椭圆', inputs: [{id: 'a', image_url: '/files/unknown.png'}], reads: [
    {input_id: 'a', channel: 'layout_inner', zone_label: '待核对文字', text: '（1)'},
    {input_id: 'a', channel: 'layout_inner', zone_label: '待核对文字', text: '③'},
  ]};
  const model = sealWorkspaceModel(item);
  assert.equal(model.text, '');
  assert.equal(model.fields[0].conflict, '（1) / ③');
  assert.equal(model.fields[0].url, '/files/unknown.png');
});

test('a managed unresolved form never falls back to a stale legacy identity', () => {
  const item = fixture(); item.receiving_form.status = 'conflict';
  item.rectangular_text = '旧仓库收货专用章②'; item.combined_text = '旧公司收货专用章②';
  const model = sealWorkspaceModel(item);
  assert.equal(model.text, '');
  assert.equal(model.status, '待核对');
  assert.doesNotMatch(renderSealWorkspace(item), /旧仓库|旧公司/);
});

test('purpose-line numbers remain literal and disagreements with separate number reads stay visible', () => {
  const item = {shape: '圆形', round_type_band_text: '收货专用章②', round_type_band_url: '/files/type.png'};
  let model = sealWorkspaceModel(item);
  assert.equal(model.fields.find(field => field.label === '印章用途').value, '收货专用章');
  assert.equal(model.fields.find(field => field.label === '印章编号').value, '②');
  item.stamp_number_text = '(2)';
  model = sealWorkspaceModel(item);
  assert.equal(model.fields.find(field => field.label === '印章编号').conflict, '(2) / ②');
});

test('processing presents compact image links and keeps the original audit nested and collapsed', () => {
  const item = {shape: '圆形', color_isolated_url: '/files/color.png', color_isolated_oriented_url: '/files/oriented.png',
    unwrapped_url: '/files/artifacts/t/ring.png', multi_zone_layout: {overlay_url: '/files/regions.png'}};
  const html = renderSealWorkspace(item, {audit: '<div>完整旧读数</div>'});
  const beforeAudit = html.split('<details class="seal-raw-record">')[0];
  assert.match(beforeAudit, /提取印章颜色/);
  assert.match(beforeAudit, /方向校正/);
  assert.match(beforeAudit, /文字分区/);
  assert.match(beforeAudit, /环形文字展开/);
  assert.doesNotMatch(beforeAudit, /<img|<details[^>]* open/);
  assert.match(html, /完整旧读数/);
});

test('remote seal results use the returned oriented image and preserve provider attributes', () => {
  const item = {provider: 'danzhengtong', provider_text: '北京恒远恒信科技发展有限公司 收发货专用章',
    provider_color: '蓝色', provider_seal_type: '椭圆章', original_url: '/files/raw.png',
    input_url: '/files/input.png', returned_image_url: '/files/returned.png', returned_image_status: 'downloaded',
    // Stale local fields must never add a synthetic purpose or company reading.
    unwrapped_text: '旧公司', round_type_band_text: '旧用途', ring_input_url: '/files/local-ring.png'};
  const before = structuredClone(item), model = sealWorkspaceModel(item);
  assert.equal(model.text, item.provider_text);
  assert.equal(model.original, '/files/input.png');
  assert.equal(model.processed, '/files/returned.png');
  assert.equal(model.preferred, '/files/returned.png');
  assert.deepEqual(model.fields.map(field => [field.label, field.value, field.url]), [
    ['印章颜色', '蓝色', '/files/returned.png'], ['印章类型', '椭圆章', '/files/returned.png']]);
  const html = renderSealWorkspace(item);
  assert.match(html, /单证通返回的旋正图/);
  assert.match(html, /data-seal-evidence-url="\/files\/returned.png"/);
  assert.doesNotMatch(html, /旧公司|旧用途|方向校正|环形文字展开|local-ring/);
  assert.deepEqual(item, before);
});

test('a failed returned image is never replaced with the uploaded colour image as oriented evidence', () => {
  const item = {ocr_backend: '单证通', provider_text: '测试公司', provider_color: '红色',
    provider_seal_type: '圆章', input_url: '/files/upload.png', color_isolated_url: '/files/upload.png',
    returned_image_status: 'unavailable', body_url: '/files/stale-local.png',
    color_isolated_oriented_url: '/files/stale-local.png'};
  const model = sealWorkspaceModel(item), html = renderSealWorkspace(item);
  assert.equal(model.original, '/files/upload.png');
  assert.equal(model.processed, '');
  assert.equal(model.preferred, '/files/upload.png');
  assert.match(html, /单证通旋正图未能取回/);
  assert.doesNotMatch(html, /stale-local/);
  assert.deepEqual(model.fields.map(row => row.url), ['', '']);
});

test('remote image urls stay local and provider text is escaped', () => {
  const item = {provider: 'danzhengtong', provider_text: '<img src=x onerror=alert(1)>',
    provider_color: '<script>x</script>', provider_seal_type: '椭圆章',
    returned_image_status: 'missing', returned_image_url: 'http://10.0.0.1/private.png'};
  const html = renderSealWorkspace(item);
  assert.equal(sealWorkspaceModel(item).processed, '');
  assert.match(html, /单证通未返回旋正图/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img|<script|10\.0\.0\.1/);
});
