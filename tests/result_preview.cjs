const test = require('node:test');
const assert = require('node:assert/strict');
const {createResultPreview, resultPreviewModel, renderResultPreview} = require('../static/modules/workbench/result_preview.mjs');
const {ReceiptWorkbench} = require('../static/modules/core/workbench.mjs');
const record = (id = 1) => ({id, filename:`${id}.jpg`, final_result:'通过', review_status:'无需复核', fields:{'要求到货':'2026-10-13','签章要求':'测试公司','仓库联系人':'王雷、李明','仓库接收人':'李明'}, date_check:{actual:'2026-10-13',status:'匹配',reliable:true}, seal_check:{recognized:'测试公司',status:'匹配',reliable:true}});
function harness() {
  const nodes = new Map(), requests = [], entered = [], notices = [], classes = new Set();
  const $ = id => {
    if (!nodes.has(id)) {
      const events = new Map();
      nodes.set(id, {dataset:{},open:false,disabled:false,isConnected:true, innerHTML:'',textContent:'',
        setAttribute(){},showModal(){this.open=true},close(){this.open=false},focus(){this.focused=true},
        addEventListener(name,fn){events.set(name,fn)},dispatch(name,event={}){return events.get(name)?.(event)}});
    }
    return nodes.get(id);
  };
  const environment={document:{body:{classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c)}}},window:{addEventListener(){}}};
  const h={$,requests,entered,notices,classes,load:async id=>record(id)};
  h.controller=createResultPreview({environment,ui:{$,toast:m=>notices.push(m)},ReceiptWorkbench,services:{records:{get:id=>{requests.push(id);return h.load(id)}}},enterReview:async id=>entered.push(id)});
  h.controller.initialize();
  h.click=selector=>$('#result-preview-dialog').dispatch('click',{target:{closest:s=>s===selector?{}:null}});
  return h;
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {resolve,reject,promise}};
test('preview uses stored comparison policy, including multiple acceptable contacts and unreliable matches',()=>{
  const item=record();
  assert.equal(resultPreviewModel(item,ReceiptWorkbench).attention.length,0);
  item.seal_check.reliable=false;
  const model=resultPreviewModel(item,ReceiptWorkbench);
  assert.equal(model.rows[1].status,'待确认');
  assert.equal(model.attention.length,1);
  assert.equal(model.rows[2].status,'匹配');
  item.date_check={status:'未执行'};
  assert.equal(resultPreviewModel(item,ReceiptWorkbench).rows[0].actual,'未执行');
});
test('excluded signature mismatch remains visible while the receipt passes without review attention',()=>{
  const item=record();
  item.fields['仓库接收人']='赵建新';
  item.recognition_config={acceptance:{date_match_mode:'any',seal_match_mode:'any',signature_match_mode:'none'}};
  item.signature_check={status:'未核对',reliable:true,source:'按设置跳过',match_mode:'none'};
  const model=resultPreviewModel(item,ReceiptWorkbench);
  assert.equal(model.status,'已通过');
  assert.equal(model.rows[2].status,'不匹配');
  assert.equal(model.rows[2].included,false);
  assert.equal(model.attention.length,0);
  assert.doesNotMatch(renderResultPreview(model),/均匹配|项待确认|不计入整单判定/);
});
test('excluded date and seal mismatches do not put a passed receipt back in the review list',()=>{
  for(const key of ['date','seal']) {
    const item=record();
    item.recognition_config={acceptance:{[`${key}_match_mode`]:'none'}};
    item[`${key}_check`].status='不匹配';
    assert.equal(ReceiptWorkbench.category(item),'passed');
    assert.equal(ReceiptWorkbench.issues(item).some(issue=>issue.target===key),false);
    assert.equal(resultPreviewModel(item,ReceiptWorkbench).attention.length,0);
    item.recognition_config.acceptance[`${key}_match_mode`]='any';
    assert.equal(ReceiptWorkbench.category(item),'review');
  }
});
test('seal review states share a pending label while preserving the original evidence',()=>{
  for (const status of ['匹配', '匹配待确认', '需人工复核']) {
    const item=record();
    item.seal_check={status,reliable:false,recognized:'测试公司',message:'文字与形状尚未共同确认'};
    const before=JSON.stringify(item);
    const model=resultPreviewModel(item,ReceiptWorkbench);
    assert.equal(model.rows[1].status,'待确认');
    assert.match(renderResultPreview(model),/待确认/);
    assert.equal(JSON.stringify(item),before);
  }
  for (const status of ['部分匹配', '不匹配', '未识别', '未执行']) {
    const item=record();item.seal_check={status,reliable:false};
    assert.equal(resultPreviewModel(item,ReceiptWorkbench).rows[1].status,status);
  }
});
test('missing results are explicit and text is escaped without rendering images',()=>{
  const item=record();delete item.date_check;
  item.seal_check.recognized='<img src=x onerror=alert(1)>';
  const html=renderResultPreview(resultPreviewModel(item,ReceiptWorkbench));
  assert.match(html,/结果未加载/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/);
});
test('opening reads only the selected receipt; only the review button enters review',async()=>{
  const h=harness(), trigger=h.$('trigger');
  await h.controller.open(7,trigger);
  assert.deepEqual(h.requests,[7]);assert.deepEqual(h.entered,[]);
  assert.equal(h.$('#result-preview-review').disabled,false);
  await h.click('#result-preview-review');
  assert.deepEqual(h.entered,[7]);assert.equal(h.$('#result-preview-dialog').open,false);
  assert.equal(trigger.focused,true);assert.equal(h.classes.size,0);
});
test('late responses cannot replace another receipt or repopulate a closed preview',async()=>{
  const h=harness(), first=deferred();h.load=id=>id===1?first.promise:Promise.resolve(record(id));
  const pending=h.controller.open(1);await h.controller.open(2);
  first.resolve(record(1));await pending;
  assert.equal(h.$('#result-preview-filename').textContent,'2.jpg');
  const late=deferred();h.load=()=>late.promise;
  const opening=h.controller.open(3);h.controller.close();late.resolve(record(3));await opening;
  assert.equal(h.$('#result-preview-review').disabled,true);
  assert.equal(h.$('#result-preview-dialog').open,false);
});
test('failed or mismatched response keeps review disabled and offers retry',async()=>{
  const h=harness();h.load=async()=>{throw Error('网络暂不可用')};
  await h.controller.open(1);assert.match(h.$('#result-preview-body').innerHTML,/重新加载/);
  assert.equal(h.$('#result-preview-review').disabled,true);
  await h.click('#result-preview-review');assert.deepEqual(h.entered,[]);
  h.load=async()=>record(2);await h.controller.open(1);
  assert.match(h.$('#result-preview-body').innerHTML,/单据结果不一致/);
});
test('Escape closes without entering review and restores focus',async()=>{
  const h=harness(), trigger=h.$('trigger');await h.controller.open(1,trigger);
  let prevented=false;await h.$('#result-preview-dialog').dispatch('cancel',{preventDefault(){prevented=true}});
  assert.equal(prevented,true);assert.equal(trigger.focused,true);assert.deepEqual(h.entered,[]);
});
