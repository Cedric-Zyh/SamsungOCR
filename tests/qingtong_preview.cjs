const test = require('node:test');
const assert = require('node:assert/strict');
const {imageSources} = require('../static/workbench.js');

function record() {
  return {id:23, review_revision:'version+1', preview_url:'/files/previews/page.jpg',
    seal_check:{api:{ok:true},dual_check:{policy:'qingtong_template_and_ocr',
      selected:{index:1,xyxy:[120,300,520,600]},candidates:[{index:0,xyxy:[1,2,30,40]},{index:1,xyxy:[120,300,520,600]}]}},
    processing_artifacts:{date:[{original_url:'/files/artifacts/date.jpg'}],seals:[{original_url:'/files/artifacts/other-local-stamp.jpg'}]}};
}

test('QingTong exposes one selected stamp alongside the whole page and date crop',()=>{
  assert.deepEqual(imageSources(record()),[
    {url:'/files/previews/page.jpg',label:'整页回单',group:'page'},
    {url:'/files/artifacts/date.jpg',label:'日期区域 1',group:'date'},
    {url:'/files/selected-seal/23.png?revision=version%2B1',label:'印章区域',group:'seal'},
  ]);
});

test('the any-channel matching policy preserves the selected QingTong stamp preview',()=>{
  const item = record(); item.seal_check.dual_check.policy = 'qingtong_any_channel';
  assert.deepEqual(imageSources(item), imageSources(record()));
  item.seal_check.dual_check.selected = null;
  assert.equal(imageSources(item).filter(source => source.group === 'seal').length, 0);
});

test('QingTong preview prefers the selected local oriented derivative when present',()=>{
  const item = record();
  item.processing_artifacts.seals = [{
    index: 1,
    api_index: 1,
    original_url:'/files/artifacts/seal-before.jpg',
    color_isolated_oriented_url:'/files/artifacts/seal-oriented.png',
    orientation:{anchor_text:'收货专用章', applied_rotation:52.4},
  }];
  assert.deepEqual(imageSources(item).find(source => source.group === 'seal'), {
    url:'/files/artifacts/seal-oriented.png', label:'印章区域 · 按章型文字旋正', group:'seal',
  });
});

test('missing or malformed selection does not substitute an unrelated local stamp',()=>{
  for(const box of [null,[],[1,2,3],[3,0,2,5],[0,0,2,Infinity],['0',0,2,5]]) {
    const item=record(); item.seal_check.dual_check.selected.xyxy=box;
    assert.equal(imageSources(item).filter(source=>source.group==='seal').length,0);
  }
  const item=record(); item.seal_check.dual_check.selected=null;
  assert.equal(imageSources(item).filter(source=>source.group==='seal').length,0);
});

test('failed API results and invalid record IDs cannot create a selected-stamp URL',()=>{
  const item=record(); item.seal_check.api.ok=false;
  assert.equal(imageSources(item).filter(source=>source.group==='seal').length,0);
  for(const id of [0,-1,'23/../24',NaN]) {
    const item=record(); item.id=id;
    assert.equal(imageSources(item).filter(source=>source.group==='seal').length,0);
  }
});

test('local-only receipt previews retain their existing crop',()=>{
  const item=record(); delete item.seal_check.dual_check;
  assert.equal(imageSources(item).find(source=>source.group==='seal').url,'/files/artifacts/other-local-stamp.jpg');
});

test('local round-stamp preview uses the post-rotation color-safe crop',()=>{
  const item=record(); delete item.seal_check.dual_check;
  item.processing_artifacts.seals = [{
    original_url:'/files/artifacts/seal-before.jpg',
    color_isolated_oriented_url:'/files/artifacts/seal-oriented.png',
    orientation:{anchor_text:'收货专用章', applied_rotation:58.2},
  }];
  assert.deepEqual(imageSources(item).find(source => source.group === 'seal'), {
    url:'/files/artifacts/seal-oriented.png', label:'印章区域 1 · 按章型文字旋正', group:'seal',
  });
});

test('QingTong rectangle preview uses the corrected crop after an automatic 180 degree rotation',()=>{
  const item = record();
  item.processing_artifacts.seals = [{
    original_url:'/files/artifacts/seal-before.jpg',
    orientation:{applied_rotation:180, status:'已自动旋转 180°'},
    orientation_corrected_url:'/files/artifacts/seal-after-180.jpg',
  }];
  assert.deepEqual(imageSources(item).find(source => source.group === 'seal'), {
    url:'/files/artifacts/seal-after-180.jpg', label:'印章区域 · 已自动旋转 180°', group:'seal',
  });
});
