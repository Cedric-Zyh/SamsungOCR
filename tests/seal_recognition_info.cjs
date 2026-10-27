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

function centerHarness(items = [1,2].map(id=>({id,revision:1,requirement:`测试${id}有限公司公章`,image_url:'/image.png'}))) {
  const {createSealCenter}=require('../static/modules/seals/center.mjs');
  const element=()=>{
    const events={},attrs=new Map(),classes=new Set();
    return {innerHTML:'',value:'',dataset:{},scrollTop:0,
      classList:{contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
      setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),
      addEventListener:(k,fn)=>events[k]=fn,fire:k=>events[k]?.({}),
      querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,scrollIntoView(){},
      insertAdjacentHTML(_position,markup){this.appendedMarkup=(this.appendedMarkup||'')+markup;}};
  };
  const nodes=new Map(), hosts=[];
  const $=selector=>{
    if(!nodes.has(selector))nodes.set(selector,element());
    return nodes.get(selector);
  };
  const detail=$('[data-standard-detail]');
  Object.defineProperty(detail,'innerHTML',{set(value){this.markup=value;this.appendedMarkup='';const host=element();hosts.push(host);nodes.set('[data-standard-recognition-info]',host)},get(){return this.markup}});
  const buttons=items.map(({id})=>Object.assign(element(),{dataset:{standardId:String(id)}}));
  const $$=selector=>selector==='[data-standard-id]'?buttons:[];
  const pending=[];
  const center=createSealCenter({ui:{$,$$,toast(){}},services:{sealStandards:{
    list:async()=>({items}),
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

test('all name directories use collapsed headings and counts, and searches retain the directory',async()=>{
  const station=(id,code)=>({id,revision:1,image_url:'/image.png',requirement:'三星电子维修中心'+code,
    name_terms:{original:'三星电子维修中心'+code,company:'三星电子维修中心',parse_status:'parsed',
      groupable:false,identity_fields:[{kind:'station_code',label:'站点编号',value:code}]}});
  const singleton={id:3,revision:1,image_url:'/image.png',requirement:'广州中启通信科技有限公司'};
  const h=centerHarness([station(1,'5972036'),station(2,'2310637'),singleton]);
  h.center.initialize();await h.center.load();
  const list=h.$('[data-standard-list]');
  assert.equal((list.innerHTML.match(/<details /g)||[]).length,2);
  assert.doesNotMatch(list.innerHTML,/seal-library-singleton|data-standard-group="[^"]+"\s+open/);
  assert.match(list.innerHTML,/同名归档/);
  assert.match(list.innerHTML,/<b>站点编号 5972036<\/b>/);
  assert.match(list.innerHTML,/<b>站点编号 2310637<\/b>/);
  assert.match(list.innerHTML,/<span class="seal-group-name">广州中启通信科技有限公司<\/span><span class="seal-group-count">1 条<\/span>/);
  assert.equal(h.$('[data-standard-summary]').textContent,'共 2 组 · 3 条');
  assert.match(h.$('[data-standard-detail]').appendedMarkup,/同组标准章/);
  assert.match(h.$('[data-standard-detail]').appendedMarkup,/站点编号 2310637/);
  assert.doesNotMatch(h.$('[data-standard-detail]').appendedMarkup,/同公司印章/);
  const search=h.$('#seal-center-search');search.value='2310637';search.fire('input');
  assert.match(list.innerHTML,/三星电子维修中心/);
  assert.match(list.innerHTML,/1 \/ 2 条/);
  assert.match(list.innerHTML,/data-standard-id="2"/);
  assert.doesNotMatch(list.innerHTML,/data-standard-id="1"|data-standard-id="3"/);
  search.value='';search.fire('input');
  assert.match(list.innerHTML,/data-standard-id="1"/);
  assert.match(list.innerHTML,/data-standard-id="3"/);
  assert.equal(h.$('[data-standard-summary]').textContent,'共 2 组 · 3 条');
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
