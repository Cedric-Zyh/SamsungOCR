const test=require('node:test');
const assert=require('node:assert/strict');
const {horizontalSettingMarkup,bindHorizontalSetting}=require('../static/modules/seals/horizontal_setting.mjs');
const {escapeHtml}=require('../static/modules/core/ui.mjs');
const {standardDetail}=require('../static/modules/seals/standard_view.mjs');

test('only round and oval standards expose the explicit three-state setting',()=>{
  for(const shape of ['round','ellipse']){
    const html=horizontalSettingMarkup({shape,horizontal_text:'absent'},escapeHtml);
    assert.match(html,/章内横排文字/);assert.match(html,/value="absent" checked/);
    assert.match(html,/未标注/);assert.match(html,/role="radiogroup"/);
    assert.match(html,/data-save-horizontal-text disabled>保存/);
    assert.doesNotMatch(html,/<select|当前：|多角度搜索/);
  }
  assert.equal(horizontalSettingMarkup({shape:'rectangle'},escapeHtml),'');
  assert.match(standardDetail({shape:'round',requirement:'测试有限公司',horizontal_text:'present'}),/value="present" checked/);
  assert.doesNotMatch(horizontalSettingMarkup({shape:'round',horizontal_text:'<img src=x>'},escapeHtml),/<img src=x>/);
});

function harness({fail=false}={}){
  const handlers={};
  const options=['present','absent','unmarked'].map(value=>({value,checked:value==='unmarked',disabled:false,
    addEventListener:(event,fn)=>handlers[`${value}:${event}`]=fn}));
  const button={disabled:false,addEventListener:(event,fn)=>handlers[event]=fn};
  const feedback={textContent:'',dataset:{}};const root={
    querySelectorAll:()=>options,
    querySelector:selector=>({'[data-save-horizontal-text]':button,'[data-horizontal-feedback]':feedback})[selector]};
  const calls=[],locks=[],saved=[];let complete;
  bindHorizontalSetting(root,{standard:{id:7,revision:3,horizontal_text:'unmarked'},
    setSaving:value=>locks.push(value),onSaved:value=>saved.push(value),
    save:(id,payload)=>{calls.push({id,payload});return new Promise((resolve,reject)=>{
      complete=()=>fail?reject(new Error('标准章已变化，请刷新')):resolve({standard:{id:7,revision:4,horizontal_text:'absent'}});
    });}});
  const choose=value=>{
    options.forEach(input=>input.checked=input.value===value);
    handlers[`${value}:change`]();
  };
  return {options,button,feedback,calls,locks,saved,choose,click:()=>handlers.click(),complete:()=>complete()};
}
test('saving sends the selected choice and current revision exactly once and locks controls',async()=>{
  const h=harness();assert(h.button.disabled);h.choose('absent');assert(!h.button.disabled);
  const pending=h.click();h.click();
  assert.equal(h.calls.length,1);assert(h.options.every(input=>input.disabled)&&h.button.disabled);
  assert.equal(h.button.textContent,'保存中…');
  assert.deepEqual(h.calls[0],{id:7,payload:{horizontal_text:'absent',standard_revision:3}});
  h.complete();await pending;assert.equal(h.saved.length,1);
  assert(h.options.every(input=>!input.disabled));assert(h.button.disabled);
  assert.match(h.feedback.textContent,/已保存/);
  h.choose('present');assert(!h.button.disabled);assert.equal(h.feedback.textContent,'');
  h.choose('absent');assert(h.button.disabled);
});
test('revision conflicts keep the draft available without reporting a saved choice',async()=>{
  const h=harness({fail:true});h.choose('absent');const pending=h.click();h.complete();await pending;
  assert.equal(h.saved.length,0);assert(h.options.find(input=>input.value==='absent').checked);
  assert.match(h.feedback.textContent,/已变化/);assert(!h.button.disabled);assert.equal(h.feedback.dataset.tone,'error');
});
test('invalid and unchanged choices do not issue a write',async()=>{
  const h=harness();await h.click();assert.equal(h.calls.length,0);
  h.choose('present');h.options[0].value='bad';await h.click();assert.equal(h.calls.length,0);
});

test('review evidence explains that the manual choice skipped searching',()=>{
  const {createReviewEvidence}=require('../static/modules/review/review_evidence.mjs');
  const target={innerHTML:''};
  createReviewEvidence({environment:{},ui:{$:()=>target},reviewState:{artifactTab:'seals',current:{processing_artifacts:{seals:[{
    index:0,schema_version:2,shape:'圆形',color:'red',
    original_url:'/files/artifacts/test/original.png',color_isolated_url:'/files/artifacts/test/color.png',
    ring_input_url:'/files/artifacts/test/ring-input.png',unwrapped_url:'/files/artifacts/test/ring.png',
    horizontal_text_policy:{setting:'absent',skipped_horizontal_search:true},reads:[],inputs:[]
  }]}}}}).renderArtifacts();
  assert.match(target.innerHTML,/标准章人工设置：无横排文字/);
  assert.match(target.innerHTML,/已跳过横排定位、多角度搜索及增强重试/);
  assert.doesNotMatch(target.innerHTML,/未检测到横向文字/);
});
