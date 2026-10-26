const assert = require('node:assert/strict');
const test = require('node:test');
const {highlightNameChanges, recognitionInfoMarkup, recognitionInfoError, bindCorrectionSources}
  = require('../static/modules/seals/recognition_info.mjs');

test('the exact corrected characters and edge deletions are highlighted and escaped', () => {
  const [raw, corrected] = highlightNameChanges('鐵深圳供座链公司臺','深圳供应链公司');
  assert.equal(raw,'<mark class="seal-name-removed">鐵</mark>深圳供<mark class="seal-name-removed">座</mark>链公司<mark class="seal-name-removed">臺</mark>');
  assert.equal(corrected,'深圳供<mark class="seal-name-added">应</mark>链公司');
  for (const text of highlightNameChanges('<script>','<svg>')) {
    assert.ok(!text.includes('<script>') && !text.includes('<svg>'));
  }
});

test('empty history explains saved records and terms do not invent examples', () => {
  const markup = recognitionInfoMarkup({terms:{supported:false},rules:[],history:[]});
  assert.match(markup,/暂无校正记录/);
  assert.match(markup,/已有读数不会自动改写/);
  assert.match(markup,/待确认/);
  assert.doesNotMatch(markup,/data-correction-source|checkbox|switch/);
});

test('real fields, branches, policy and raw history are escaped; sample provenance is retained', () => {
  const markup=recognitionInfoMarkup({terms:{company:'<公司>',branch:'南京分公司',supported:true},
    branches:['南京分公司','<其他>'],rules:[{title:'规则',description:'<说明>',state:'已启用'}],
    history:[{result_id:7,filename:'<来源>.jpg',source_kind:'sample',raw_text:'供座链',corrected_text:'供应链',
      changes:[{rule:'known_ocr_word'}],created_at:'2026-10-26T10:00:00'}]});
  assert.match(markup,/&lt;公司&gt;/);assert.match(markup,/&lt;来源&gt;.jpg/);
  assert.match(markup,/data-correction-source="7" data-source-kind="sample"/);
  assert.match(markup,/is-current/);assert.match(markup,/固定错词/);
  assert.doesNotMatch(markup,/<公司>|<来源>|<说明>/);
  assert.doesNotMatch(recognitionInfoError('<错误>'),/<错误>/);
});

test('source opens only once while pending and recovers from errors', async () => {
  let click, resolve, calls=[], toasts=[];
  const attrs=new Map();
  const button={dataset:{correctionSource:'7',sourceKind:'sample'},
    setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),
    addEventListener:(_,fn)=>click=fn};
  bindCorrectionSources({}, {ui:{$$:()=>[button],toast:message=>toasts.push(message)},
    openSource:(...args)=>{calls.push(args);return new Promise(r=>resolve=r);}});
  const pending=click();await click();assert.deepEqual(calls,[[7,'sample']]);
  assert.equal(attrs.get('aria-busy'),'true');resolve();await pending;
  assert.equal(attrs.has('aria-busy'),false);
  bindCorrectionSources({}, {ui:{$$:()=>[button],toast:message=>toasts.push(message)},
    openSource:async()=>{throw Error('来源不存在');}});
  await click();assert.deepEqual(toasts,['来源不存在']);
  assert.equal(attrs.has('aria-busy'),false);
});

function centerHarness() {
  const {createSealCenter}=require('../static/modules/seals/center.mjs');
  const element=()=>{
    const events={},attrs=new Map(),classes=new Set();
    return {innerHTML:'',value:'',dataset:{},scrollTop:0,
      classList:{contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
      setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),
      addEventListener:(k,fn)=>events[k]=fn,fire:k=>events[k]?.({}),
      querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,scrollIntoView(){},
      insertAdjacentHTML(){}};
  };
  const nodes=new Map(), hosts=[];
  const $=selector=>{
    if(!nodes.has(selector))nodes.set(selector,element());
    return nodes.get(selector);
  };
  const detail=$('[data-standard-detail]');
  Object.defineProperty(detail,'innerHTML',{set(value){this.markup=value;const host=element();hosts.push(host);nodes.set('[data-standard-recognition-info]',host)},get(){return this.markup}});
  const buttons=[1,2].map(id=>Object.assign(element(),{dataset:{standardId:String(id)}}));
  const $$=selector=>selector==='[data-standard-id]'?buttons:[];
  const pending=[];
  const center=createSealCenter({ui:{$,$$,toast(){}},services:{sealStandards:{
    list:async()=>({items:[1,2].map(id=>({id,revision:1,requirement:`测试${id}有限公司公章`,image_url:'/image.png'}))}),
    recognitionInfo:id=>new Promise((resolve,reject)=>pending.push({id,resolve,reject}))
  }}});
  return {center,pending,buttons,hosts,$};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const info=(id,company)=>({standard_id:id,revision:1,terms:{company,supported:true},rules:[],history:[]});

test('switching archive ignores a late correction-info response from the previous stamp',async()=>{
  const h=centerHarness();await h.center.load();
  assert.equal(h.pending[0].id,1);
  h.buttons[1].fire('click');
  h.pending[1].resolve(info(2,'当前档案'));await flush();
  assert.match(h.hosts[1].innerHTML,/当前档案/);
  h.pending[0].resolve(info(1,'旧档案'));await flush();
  assert.match(h.hosts[1].innerHTML,/当前档案/);
  assert.doesNotMatch(h.hosts[1].innerHTML,/旧档案/);
});

test('load errors allow retry and a changed revision cannot silently show mismatched rules',async()=>{
  const h=centerHarness();await h.center.load();
  h.pending[0].reject(Error('连接失败'));await flush();
  assert.match(h.hosts[0].innerHTML,/连接失败/);
  h.$('[data-name-info-retry]').fire('click');
  h.pending[1].resolve({...info(1,'不应展示'),revision:2});await flush();
  assert.match(h.hosts[0].innerHTML,/档案已更新/);
  assert.doesNotMatch(h.hosts[0].innerHTML,/不应展示/);
});

test('terms show store identity, independent codes and partial leftovers in separate fields',()=>{
  const store=recognitionInfoMarkup({terms:{company:'华润万象汇三星授权体验店',entity_type:'授权体验店',
    supported:true,parse_status:'parsed',purpose:'',number:'',identity_fields:[]},rules:[]});
  assert.match(store,/<dt>主体名称<\/dt><dd>华润万象汇三星授权体验店/);
  assert.match(store,/<dt>印章用途<\/dt><dd>未注明/);
  assert.doesNotMatch(store,/公司主体|未单列分公司/);
  const warehouse=recognitionInfoMarkup({terms:{company:'广州花东仓',supported:true,parse_status:'parsed',
    purpose:'收货专用章',number:'①/②',identity_fields:[{label:'仓库代码',value:'020DCH'}]},rules:[]});
  assert.match(warehouse,/<dt>仓库代码<\/dt><dd>020DCH/);
  assert.match(warehouse,/<dt>章内编号<\/dt><dd>①\/②/);
  const partial=recognitionInfoMarkup({terms:{company:'北京某有限公司',supported:true,parse_status:'partial',
    unclassified:'<北>',purpose:'售后服务专用章'},rules:[]});
  assert.match(partial,/部分拆分/);assert.match(partial,/<dt>未分类文字<\/dt><dd>&lt;北&gt;/);
  assert.match(partial,/暂不启用/);
});

test('alternative wordings display separate purpose and number pairs under one owner',()=>{
  const owner='青岛和沃电子技术有限公司';
  const markup=recognitionInfoMarkup({terms:{company:owner,supported:true,parse_status:'parsed',
    alternatives:[{purpose:'业务专用章',number:''},{purpose:'维修业务专用章',number:'(1)'}]},rules:[]});
  assert.equal((markup.match(new RegExp(owner,'g'))||[]).length,1);
  assert.match(markup,/写法 1<\/h4>[\s\S]*业务专用章[\s\S]*<dt>章内编号<\/dt><dd>未注明/);
  assert.match(markup,/写法 2<\/h4>[\s\S]*维修业务专用章[\s\S]*<dt>章内编号<\/dt><dd>\(1\)/);
});
