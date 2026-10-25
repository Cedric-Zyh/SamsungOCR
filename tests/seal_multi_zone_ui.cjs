const test = require('node:test');
const assert = require('node:assert/strict');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');

test('double-border company and website have separate previews with raw evidence retained', () => {
  const target = {innerHTML: ''};
  const item = {schema_version:2,index:0,color:'red',shape:'椭圆',
    double_border_layout:{status:'adopted',overlay_url:'/files/artifacts/test/borders.png',
      primary:{image_url:'/files/artifacts/test/old.png',text:'MMM旧环字O'}},
    unwrapped_url:'/files/samples/artifacts/task/company.png',unwrapped_text:'测试京东维修中心',
    website_url:'/files/samples/artifacts/task/website.png',website_text:'www.JD.com',
    round_type_band_url:'/files/artifacts/test/purpose.png',round_type_band_text:'维修专用章'};
  const reviewState={artifactTab:'seals',current:{processing_artifacts:{seals:[item]}}};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState}).renderArtifacts();
  assert.match(target.innerHTML,/公司名称展开图（双椭圆边框之间）/);
  assert.match(target.innerHTML,/底部网址（单独保留）/);
  assert.match(target.innerHTML,/company.png\?preview=rgb/);
  assert.match(target.innerHTML,/website.png\?preview=rgb/);
  assert.match(target.innerHTML,/原整环识别（仅供核对）/);
  assert.ok(target.innerHTML.indexOf('测试京东维修中心') < target.innerHTML.indexOf('<details>'));
  assert.ok(target.innerHTML.indexOf('MMM旧环字O') > target.innerHTML.indexOf('<details>'));
});

test('multi-zone evidence shows actual separate inputs and RGB arc previews', () => {
  const target = {innerHTML: ''};
  const item = {schema_version:2,index:0,color:'red',shape:'椭圆',
    original_url:'/files/artifacts/test/original.png',color_isolated_oriented_url:'/files/artifacts/test/enhanced.png',
    ring_input_url:'/files/artifacts/test/masked.png',multi_zone_layout:{overlay_url:'/files/artifacts/test/zones.png'},
    inputs:[{id:'row',zone_name:'inner-1',label:'内部文字 1',image_url:'/files/artifacts/test/row.png'},
      {id:'upper',zone_name:'upper',label:'上弧文字',image_url:'/files/artifacts/test/upper.png'},
      {id:'lower',zone_name:'lower',label:'下弧文字',image_url:'/files/artifacts/test/lower.png'}],
    reads:[{input_id:'row',channel:'layout_inner',zone_label:'站点编号',text:'5785258站<',confidence:.99},
      {input_id:'upper',channel:'ring',zone_label:'上弧名称',text:'三星电子服务中心',confidence:.99},
      {input_id:'lower',channel:'layout_lower',zone_label:'下弧电话',text:'电话：020-31103112',confidence:.99}]};
  const reviewState={artifactTab:'seals',current:{processing_artifacts:{seals:[item]}}};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState}).renderArtifacts();
  assert.match(target.innerHTML,/印章分区识别/);
  assert.match(target.innerHTML,/自动文字分区/);
  assert.match(target.innerHTML,/站点编号/);
  assert.match(target.innerHTML,/5785258站&lt;/);
  assert.match(target.innerHTML,/下弧电话/);
  assert.match(target.innerHTML,/upper.png\?preview=rgb/);
  assert.match(target.innerHTML,/lower.png\?preview=rgb/);
  assert.doesNotMatch(target.innerHTML,/无横向文字，直接/);
});

test('blue department rows and full-ring company input remain visible', () => {
  const target = {innerHTML: ''};
  const item = {schema_version:2,index:0,color:'blue',shape:'椭圆',
    original_url:'/files/artifacts/test/original.png',
    color_isolated_oriented_url:'/files/artifacts/test/enhanced.png',
    ring_input_url:'/files/artifacts/test/masked.png',
    multi_zone_layout:{method:'ellipse_department_rows',overlay_url:'/files/artifacts/test/zones.png'},
    inputs:[{id:'department',zone_name:'inner-1',channel:'layout_inner',image_url:'/files/artifacts/test/department.png'},
      {id:'company',zone_name:'company',channel:'ring',image_url:'/files/artifacts/test/company.png'}],
    reads:[{input_id:'department',channel:'layout_inner',zone_label:'章内部门',text:'仓储部',confidence:.94},
      {input_id:'company',channel:'ring',zone_label:'环形公司名称',text:'测试科技有限公司',confidence:.99}]};
  const reviewState={artifactTab:'seals',current:{processing_artifacts:{seals:[item]}}};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState}).renderArtifacts();
  assert.match(target.innerHTML,/章内部门/);
  assert.match(target.innerHTML,/仓储部/);
  assert.match(target.innerHTML,/company.png\?preview=rgb/);
});

test('mixed ring evidence labels the selected Han input and full audit image', () => {
  const target = {innerHTML: ''};
  const item = {schema_version:2,index:0,color:'red',shape:'圆形',
    original_url:'/files/artifacts/test/original.png',
    ring_input_url:'/files/artifacts/test/stamp.png',
    ring_text_band_url:'/files/artifacts/test/han-band.png',
    ring_han_selection:{method:'verified_han_company_arc'},
    unwrapped_url:'/files/artifacts/test/han-line.png',
    unwrapped_full_url:'/files/artifacts/test/mixed-full.png',
    unwrapped_text:'某某测试电子有限公司'};
  const reviewState={artifactTab:'seals',current:{processing_artifacts:{seals:[item]}}};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState}).renderArtifacts();
  assert.match(target.innerHTML,/汉字段展开图（公司名称识别输入）/);
  assert.match(target.innerHTML,/其他文字与数字仅供核对/);
  assert.match(target.innerHTML,/不参与公司名称匹配/);
  assert.doesNotMatch(target.innerHTML,/环形 OCR 直接读取旋正后的整章图/);
});

test('continuous company input is primary and replaced split reads stay in audit details', () => {
  const target = {innerHTML: ''};
  const item = {schema_version:2,index:0,color:'red',shape:'圆形',
    ring_input_url:'/files/artifacts/test/masked.png',
    multi_zone_layout:{method:'ellipse_continuous_name'},
    inputs:[
      {id:'upper',zone_name:'upper',superseded_by:'company',image_url:'/files/artifacts/test/upper.png'},
      {id:'lower',zone_name:'lower',superseded_by:'company',image_url:'/files/artifacts/test/lower.png'},
      {id:'company',zone_name:'company',channel:'ring',image_url:'/files/artifacts/test/company.png'}],
    reads:[{input_id:'company',channel:'ring',zone_label:'环形公司名称',text:'完整公司名称'},
      {input_id:'upper',channel:'split_arc_diagnostic',zone_label:'原分段读数（仅供核对）',text:'截断文字'}]};
  const reviewState={artifactTab:'seals',current:{processing_artifacts:{seals:[item]}}};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState}).renderArtifacts();
  assert.match(target.innerHTML,/沿连续外环完整展开公司名称/);
  assert.match(target.innerHTML,/原分段识别（仅供核对）/);
  assert.match(target.innerHTML,/company.png\?preview=rgb/);
  assert.ok(target.innerHTML.indexOf('完整公司名称') < target.innerHTML.indexOf('<details>'));
  assert.ok(target.innerHTML.indexOf('截断文字') > target.innerHTML.indexOf('<details>'));
});

test('sample-center grey ring images also request RGB-compatible previews', () => {
  const target = {innerHTML: ''};
  const item = {schema_version:2,index:0,color:'red',shape:'圆形',
    multi_zone_layout:{method:'ellipse_continuous_name'},
    inputs:[{id:'company',zone_name:'company',channel:'ring',
      image_url:'/files/samples/artifacts/task/seals/company.png'}],
    reads:[{input_id:'company',text:'测试公司',zone_label:'环形公司名称',channel:'ring'}]};
  const reviewState={artifactTab:'seals',current:{processing_artifacts:{seals:[item]}}};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState}).renderArtifacts();
  assert.match(target.innerHTML,/\/files\/samples\/artifacts\/task\/seals\/company.png\?preview=rgb/);
});
