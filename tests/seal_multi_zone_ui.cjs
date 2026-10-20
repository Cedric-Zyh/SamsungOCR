const test = require('node:test');
const assert = require('node:assert/strict');
const {createReviewEvidence} = require('../static/modules/review/review_evidence.mjs');

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
