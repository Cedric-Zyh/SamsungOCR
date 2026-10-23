const test = require('node:test');
const assert = require('node:assert/strict');
const {matchesResultFilters, createResultFilters, sampleFilterEvidence} = require('../static/modules/samples/result_filters.mjs');
const {createSampleCenter, sampleScope} = require('../static/modules/samples/center.mjs');
const cell = (state, value = '', accuracy = '待标注') => ({state,value,recognized:Boolean(value),accuracy});
const items = [
  {filename:'one.jpg',company:'甲',status:'已完成',result_id:1,signature:'匹配',result_filters:{
    date:cell('未识别'),seals:{combined:cell('匹配','综合章'),qingtong_template:cell('匹配','甲章'),qingtong_ocr:cell('不匹配','乙章','正确')}}},
  {filename:'two.jpg',company:'甲',status:'已完成',result_id:2,signature:'不匹配',result_filters:{
    date:cell('不匹配','2025-02-13','正确'),seals:{combined:cell('匹配','综合章'),qingtong_template:cell('匹配','甲章'),qingtong_ocr:cell('匹配','甲章','错误')}}},
  {filename:'other.jpg',company:'乙',status:'已完成',result_id:3,signature:'匹配',result_filters:{
    date:cell('未识别'),seals:{qingtong_ocr:cell('不匹配','乙章')}}},
];
function nodes() {
  const map = new Map(), store = new Map();
  const $ = selector => {
    if (!map.has(selector)) map.set(selector, {value:'',dataset:{},innerHTML:'',textContent:'',scrollTop:0,open:false,
      hidden:false,disabled:false,handlers:{},attributes:{},classList:{toggle(){},add(){},remove(){}},
      addEventListener(name,fn){this.handlers[name]=fn;},setAttribute(name,value){this.attributes[name]=value;},
      contains(){return false;},focus(){},
    });
    return map.get(selector);
  };
  const environment = {document:{activeElement:null},window:{addEventListener(){},localStorage:{
    getItem:key=>store.get(key),setItem:(key,value)=>store.set(key,value)}}};
  return {$,environment};
}

test('filters compose across company, date and independent seal channels', () => {
  const filters = {date:'未识别',sealSource:'qingtong_ocr',seal:'不匹配',compareSource:'qingtong_template',compareSeal:'匹配'};
  assert.deepEqual(sampleScope(items,{company:'甲',resultFilters:filters}),[items[0]]);
  assert.equal(matchesResultFilters(items[0],{seal:'匹配'}),true);
  assert.equal(matchesResultFilters(items[0],{sealSource:'qingtong_ocr',seal:'匹配'}),false);
  assert.equal(matchesResultFilters(items[1],{date:'recognized',dateAccuracy:'正确',signature:'不匹配'}),true);
  assert.equal(matchesResultFilters(items[1],{date:'匹配',dateAccuracy:'正确'}),false);
});

test('not run, failed, missing and pending channel states remain separate', () => {
  for (const state of ['未运行','调用失败','缺少结果','未识别','测试中']) {
    const item = {status:'已完成',result_filters:{date:cell(state),seals:{qingtong_ocr:cell(state)}}};
    assert(matchesResultFilters(item,{date:state,sealSource:'qingtong_ocr',seal:state}));
    assert(!matchesResultFilters(item,{date:'recognized'}));
    assert.equal(matchesResultFilters(item,{date:'未识别'}),state==='未识别');
  }
  assert(matchesResultFilters({status:'测试中'},{date:'测试中',signature:'测试中'}));
  assert(!matchesResultFilters({status:'测试中',signature:'匹配'},{signature:'匹配'}));
});

test('folding keeps active conditions and count, remembers preference, and supports removing one chip', () => {
  const {$,environment} = nodes();
  let changes=0;
  const filters = createResultFilters({environment,ui:{$},changed:()=>changes++});
  filters.initialize();
  assert.equal($('#sample-result-filters-body').hidden,true);
  $('#sample-result-filters-toggle').handlers.click();
  assert.equal($('#sample-result-filters-toggle').attributes['aria-expanded'],'true');
  $('#sample-filter-date').value='未识别';
  $('#sample-filter-date').handlers.change();
  filters.render(12,100,false,false);
  assert.match($('#sample-result-filter-chips').innerHTML,/日期：未识别/);
  assert.equal($('#sample-result-filters-count').textContent,'符合条件 12 张');
  $('#sample-result-filters-toggle').handlers.click();
  assert.equal($('#sample-result-filters-body').hidden,true);
  filters.render(9,100,false,false);
  assert.equal(filters.current().date,'未识别');
  assert.match($('#sample-result-filter-chips').innerHTML,/日期：未识别/);
  assert.equal($('#sample-result-filters-count').textContent,'符合条件 9 张');
  $('#sample-result-filter-chips').handlers.click({target:{closest:()=>({dataset:{clearResultFilter:'date'}})}});
  assert.equal(filters.current().date,'');
  assert.equal(changes,2);
  $('#sample-result-filters-toggle').handlers.click();
  const second=createResultFilters({environment,ui:{$},changed:()=>{}});
  second.initialize();
  assert.equal($('#sample-result-filters-body').hidden,false);
});

test('source evidence is independent and escapes values embedded in card attributes', () => {
  const item = {status:'已完成',result_filters:{seals:{qingtong_ocr:cell('不匹配','<img onerror="bad">')}}};
  const html = sampleFilterEvidence(item,{sealSource:'qingtong_ocr'});
  assert.match(html,/清瞳 · 文字 OCR：不满足要求/);
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<img/);
});

test('library filters all pages, resets pagination and selection, and runs only matching samples', async () => {
  const {$,environment} = nodes();
  const rows = [...Array.from({length:8},(_,i)=>({...structuredClone(items[0]),filename:`match-${i}.jpg`})),
    {...structuredClone(items[1]),filename:'nonmatch.jpg'},structuredClone(items[2])];
  let calls=[];
  const center=createSampleCenter({environment,ui:{$,$$:()=>[],toast(){}},flushSettings:async()=>{},
    services:{samples:{list:async()=>({items:rows,companies:[{name:'甲',count:9},{name:'乙',count:1}]}),
      run:async names=>{calls.push(names);return {accepted:names,skipped:[]};}}}});
  center.initialize();await center.load();
  $('#sample-next').handlers.click();
  assert.equal($('#sample-page-number').textContent,'2 / 2');
  $('#sample-filter-date').value='未识别';$('#sample-filter-date').handlers.change();
  assert.equal($('#sample-page-number').textContent,'1 / 2');
  assert.match($('#sample-page-info').textContent,/共 9 张/);
  $('#sample-company-list').handlers.click({target:{closest:()=>({dataset:{company:'甲',requirement:''}})}});
  assert.match($('#sample-page-info').textContent,/共 8 张/);
  // Select one visible item, then changing a result condition clears it.
  $('#sample-grid').handlers.change({target:{matches:()=>true,checked:true,closest:()=>({dataset:{filename:'match-0.jpg'}})}});
  assert.match($('#sample-clear-selection').textContent,/已选 1 张/);
  $('#sample-filter-seal-source').value='qingtong_ocr';$('#sample-filter-seal-source').handlers.change();
  $('#sample-filter-seal').value='不匹配';$('#sample-filter-seal').handlers.change();
  assert.match($('#sample-clear-selection').textContent,/已选 0 张/);
  await $('#sample-result-filters-run').handlers.click();
  assert.deepEqual(calls,[Array.from({length:8},(_,i)=>`match-${i}.jpg`)]);
});
