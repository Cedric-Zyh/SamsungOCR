const test = require('node:test');
const assert = require('node:assert/strict');
const {sealWorkspaceModel, renderSealWorkspace, sealImageUrl} = require('../static/modules/review/seal_workspace.mjs');

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
