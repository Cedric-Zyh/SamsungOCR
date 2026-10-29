const test = require('node:test');
const assert = require('node:assert/strict');
const {dateWorkspaceModel, requirementWorkspaceModel, renderRegionWorkspace, bindRegionWorkspace} = require('../static/modules/review/region_workspace.mjs');

test('date summary uses the saved decision, never promotes a preprocessing read to a confirmed result', () => {
  const record = {fields: {'要求到货': '2026-02-25'}, date_check: {actual: '', status: '未识别'}, processing_artifacts: {date: [{
    variant: '日期区域', date_line_original_url: '/files/original.jpg', date_line_color_clean_url: '/files/color.png',
    date_line_positioned_frame_clean_url: '/files/frame.png',
    date_line_ocr_variants: [{preprocessing: '日期行去印章色', ocr_texts: ['2026年2月25日']}],
    date_line_display_ocr_variants: [{preprocessing: '日期行去外框', ocr_texts: ['2026年2月26日']}],
  }]}};
  const before = structuredClone(record), model = dateWorkspaceModel(record);
  assert.equal(model.result, ''); assert.equal(model.status, '未识别');
  assert.deepEqual(model.steps.map(step => step.text), ['', '2026年2月25日', '2026年2月26日']);
  const html = renderRegionWorkspace(model);
  assert.match(html, /未识别到完整日期/); assert.match(html, /2026年2月26日/);
  assert.doesNotMatch(html, /<img|<details[^>]+ open/);
  assert.deepEqual(record, before);
});

test('date prefers the current region but retains legacy reads and does not infer a match from equal dates', () => {
  const model = dateWorkspaceModel({date_check: {actual: '2026-02-25', required: '2026-02-25', status: '匹配', reliable: false},
    processing_artifacts: {date: [{variant: '紧凑区域', original_url: '/files/old.png', ocr_variants: [{ocr_texts: ['旧读数']}]},
      {variant: '日期区域', original_url: '/files/current.png'}]}});
  assert.equal(model.original, '/files/current.png'); assert.equal(model.status, '匹配待确认');
  assert.match(renderRegionWorkspace(model), /旧读数/);
  assert.equal(dateWorkspaceModel({processing_artifacts: {date: [{original_url:'/files/legacy.png'}]}}).original, '/files/legacy.png');
});

test('requirement keeps edited text separate from raw OCR, wraps full text and uses only valid saved page coordinates', () => {
  const model = requirementWorkspaceModel({preview_url: '/files/page.jpg', fields: {'签章要求': '客户收货章②'},
    processing_artifacts: {signature_requirement: [{original_url: '/files/original.png', color_clean_url: '/files/clean.png',
      ocr_text: '签章要求:客户收货章2<script>', ocr_scope: 'clean_page', crop_box: [20,400,980,450], page_size: [1000,1500]}]}});
  assert.equal(model.result, '客户收货章②');
  assert.deepEqual(model.location, [.02, 400/1500, .96, 50/1500]);
  const html = renderRegionWorkspace(model);
  assert.match(html, /整页识别结果/); assert.match(html, /查看原图位置/);
  assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script>|<img|二次识别/);
  assert.equal(requirementWorkspaceModel({preview_url: '/files/page.jpg', processing_artifacts: {signature_requirement: [{crop_box: [0,0,1500,50], page_size:[1000,1500]}]}}).pageUrl, '');
});

test('missing images retain the result and raw readings without offering unavailable location links', () => {
  const html = renderRegionWorkspace(requirementWorkspaceModel({fields:{'签章要求':'实际签章要求'}}));
  assert.match(html, /实际签章要求/); assert.match(html, /未保存区域处理图片/);
  assert.doesNotMatch(html, /data-region-evidence-url|data-region-location/);
});

test('region image and page-location actions use separate sources without mutating the result', () => {
  const actions = [], callbacks = {};
  const image = {dataset:{regionEvidenceUrl:'/files/processed.png',regionEvidenceLabel:'去外框'}, setAttribute() {}, addEventListener: (key, fn) => callbacks.image = fn};
  const root = {querySelectorAll: () => [image], querySelector: () => ({addEventListener: (key, fn) => callbacks.location = fn})};
  const model = {group:'requirement',label:'签章要求',original:'/files/original.png',processed:'/files/processed.png',pageUrl:'/files/page.jpg',location:[.1,.5,.8,.03]};
  const show = bindRegionWorkspace(root, model, detail => actions.push(detail));
  show(); callbacks.image(); callbacks.location();
  assert.deepEqual(actions.map(action => [action.url, action.location]), [['/files/original.png', null],['/files/processed.png',null],['/files/page.jpg',[.1,.5,.8,.03]]]);
});
