const assert = require('node:assert/strict');
const test = require('node:test');
const {createReviewQueue} = require('../static/modules/review_queue.mjs');
const {normalizeReviewScope, reviewScopeRequest} = require('../static/modules/review_scope.mjs');
const workbench = require('../static/workbench.js');

const row = id => ({id, filename:`${id}.jpg`, fields:{客户名称:'甲客户'}, review_status:'待复核'});
const page = (items, total=items.length, number=1) => ({items,total,page:number,page_size:100});
function harness() {
  const nodes = new Map(), requests = [], opened = [];
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value:'',textContent:'',innerHTML:'',disabled:false,
      listeners:{}, addEventListener(type, listener) { this.listeners[type] = listener; },
      classList:{toggle(){}}, setAttribute(){}});
    return nodes.get(selector);
  };
  const state = {reviewDeferred:new Set(), current:null}, location = {hash:'#records'};
  const environment = {location, history:{replaceState(_a,_b,hash){location.hash=hash;}},
    URLSearchParams,AbortController,document:{body:{dataset:{activePage:'records'}}},window:{scrollY:30}};
  const options = {environment,ui:{$,$$:()=>[],toast(){}},reviewState:state,ReceiptWorkbench:workbench,
    api:async(url) => {requests.push(url);return options.response(url);},response:()=>page([row(1)]),
    canLeaveReview:()=>options.leaveAllowed !== false,openReview:async id=>{opened.push(id);state.current=row(id);},
    showReviewEmpty:()=>{state.current=null;},showPage:page=>{environment.document.body.dataset.activePage=page;}};
  const queue = createReviewQueue(options);
  return {queue,state,$,requests,opened,options,environment};
}

test('filtered scope snapshots every filter and retains it through paging, refresh and next', async () => {
  const h=harness(), filters={customer:'甲客户',import_date:'2026-09-10',search:'订单'};
  h.options.response=url=>{const p=new URL(url,'http://local').searchParams;return page([row(Number(p.get('page')))],201,Number(p.get('page')));};
  await h.queue.startReviewScope({kind:'filtered',label:'客户：甲客户',filters},{recordId:101});
  filters.customer='乙客户';
  await h.queue.loadReviewQueue(false,{page:2});
  await h.queue.refreshReviewScope();
  await h.queue.continueReview();
  for (const url of h.requests) {
    const parsed=new URL(url,'http://local');
    assert.equal(parsed.pathname,'/api/results');
    assert.equal(parsed.searchParams.get('customer'),'甲客户');
    assert.equal(parsed.searchParams.get('search'),'订单');
    assert.equal(parsed.searchParams.get('import_date'),'2026-09-10');
    assert.equal(parsed.searchParams.get('reviewable'),'1');
  }
  assert.equal(h.$('#review-scope-label').textContent,'客户：甲客户');
  assert.equal(h.opened[0],101);
  assert.equal(h.state.reviewOrigin.hash,'#records');
});

test('selected scope uses server IDs on every page and an empty selection is explicit', async () => {
  const h=harness(), ids=Array.from({length:105},(_,i)=>i+1);
  h.options.response=url=>{const n=Number(new URL(url,'http://local').searchParams.get('page'));
    return page(ids.slice((n-1)*100,n*100).map(row),ids.length,n);};
  await h.queue.startReviewScope({kind:'selected',ids});
  await h.queue.loadReviewQueue(false,{page:2});
  ids.push(999);
  assert.deepEqual(h.state.reviewScope.ids,Array.from({length:105},(_,i)=>i+1));
  assert(h.requests.every(url=>new URL(url,'http://local').searchParams.get('ids').split(',').length===105));
  const empty=normalizeReviewScope({kind:'selected',ids:[]});
  const params=new URL(reviewScopeRequest(empty,1,URLSearchParams),'http://local').searchParams;
  assert.equal(params.has('ids'),true);assert.equal(params.get('ids'),'');
  assert.throws(()=>normalizeReviewScope({kind:'selected',ids:[1,'bad']}),/编号/);
});

test('new scope resets session only after unsaved edits permit leaving', async () => {
  const h=harness();
  await h.queue.startReviewScope({kind:'filtered',filters:{customer:'甲客户'}});
  h.queue.markReviewSaved(1,'待复核');
  const session=h.state.reviewSessionId;
  h.options.leaveAllowed=false;
  assert.equal(await h.queue.startReviewScope({kind:'day',filters:{import_date:'2026-09-11'}}),false);
  assert.equal(h.state.reviewScope.filters.customer,'甲客户');
  assert.equal(h.state.reviewSessionId,session);assert(h.state.reviewDeferred.has(1));
  h.options.leaveAllowed=true;
  await h.queue.startReviewScope({kind:'day',filters:{import_date:'2026-09-11'}});
  assert.equal(h.state.reviewDeferred.size,0);assert.equal(h.state.reviewProcessed.size,0);
  assert.equal(h.state.reviewScope.kind,'day');
  assert.equal(new URL(h.requests.at(-1),'http://local').pathname,'/api/daily-results');
});

test('session progress does not count repeated saves twice and refreshing retains deferrals', async () => {
  const h=harness(); h.options.response=()=>page([row(1),row(2),row(3)],3);
  await h.queue.startReviewScope({kind:'selected',ids:[1,2,3]});
  h.queue.markReviewSaved(1,'确认通过');h.queue.markReviewSaved(1,'确认通过');
  h.queue.markReviewSaved(2,'待复核');h.queue.markReviewSaved(2,'待复核');
  assert.equal(h.$('#review-session-progress').textContent,'已处理 1 张 · 待复核 1 张 · 稍后 1 张');
  h.options.response=()=>({...page([row(2),row(3)],2),deferred_in_scope_ids:[2]});
  await h.queue.refreshReviewScope();
  assert(h.state.reviewDeferred.has(2));assert(h.state.reviewProcessed.has(1));
  await h.queue.continueReview();assert.equal(h.opened.at(-1),3);
  assert.equal(h.$('#review-session-progress').textContent,'已处理 1 张 · 待复核 1 张 · 稍后 1 张');
});

test('refresh keeps the source filters and current edits without restarting the session', async () => {
  const h=harness(); h.queue.initialize();
  await h.queue.startReviewScope({kind:'filtered',filters:{customer:'甲客户',import_date:'2026-09-10'}});
  const session=h.state.reviewSessionId, current=h.state.current;
  h.options.leaveAllowed=false;
  await h.$('#review-refresh').listeners.click();
  assert.equal(h.state.reviewDay,'2026-09-10');
  assert.equal(h.state.reviewScope.kind,'filtered');
  assert.equal(h.state.reviewScope.filters.customer,'甲客户');
  assert.equal(h.state.reviewSessionId,session);
  assert.equal(h.state.current,current);
  assert.equal(h.opened.length,1);
  const params=new URL(h.requests.at(-1),'http://local').searchParams;
  assert.equal(params.get('customer'),'甲客户');
  assert.equal(params.get('import_date'),'2026-09-10');
  assert.equal(h.$('#review-session-progress').textContent,'待复核 1 张');
});

test('late responses from a previous scope cannot replace the new queue or open a receipt', async () => {
  const h=harness(); let resolveOld;
  h.options.response=()=>new Promise(resolve=>{resolveOld=resolve;});
  const old=h.queue.startReviewScope({kind:'filtered',filters:{customer:'旧客户'}});
  h.options.response=()=>page([row(2)]);
  await h.queue.startReviewScope({kind:'filtered',filters:{customer:'新客户'}});
  resolveOld(page([row(1)]));await old;
  assert.deepEqual(h.opened,[2]);assert.equal(h.state.reviewQueue[0].id,2);
});

test('viewing an already confirmed receipt does not subtract unrelated pending work', async () => {
  const h=harness(); h.options.response=()=>page([row(1),row(2)],2);
  await h.queue.startReviewScope({kind:'filtered',filters:{customer:'甲客户'}},{recordId:9});
  h.queue.ensureReviewScope({...row(9),review_status:'确认通过'});
  h.queue.markReviewSaved(9,'确认通过');
  assert.equal(h.$('#review-session-progress').textContent,'已处理 1 张 · 待复核 2 张');
  h.queue.markReviewSaved(9,'待复核');
  assert.equal(h.$('#review-session-progress').textContent,'待复核 2 张 · 稍后 1 张');
});

test('remaining count excludes only deferred IDs still matching the server scope across all pages', async () => {
  const h=harness();h.options.response=()=>({...page([row(1),row(2)],102),deferred_in_scope_ids:[]});
  await h.queue.startReviewScope({kind:'filtered',filters:{date_status:'匹配待确认'}});
  h.queue.markReviewSaved(1,'待复核');
  // Editing the date moved this receipt outside the active date-status filter.
  h.options.response=()=>({...page([row(2)],101),deferred_in_scope_ids:[]});
  await h.queue.continueReview();
  assert.equal(h.$('#review-session-progress').textContent,'待复核 101 张 · 稍后 1 张');
  assert.equal(new URL(h.requests.at(-1),'http://local').searchParams.get('deferred_ids'),'1');
  // Another deferred receipt is on a page that is not currently loaded.
  h.queue.ensureReviewScope(row(150));h.queue.markReviewSaved(150,'待复核');
  h.options.response=()=>({...page([row(2)],101),deferred_in_scope_ids:[150]});
  await h.queue.refreshReviewScope();
  assert.equal(h.$('#review-session-progress').textContent,'待复核 100 张 · 稍后 2 张');
  // A different window confirms it; session history remains, pending does not.
  h.options.response=()=>({...page([row(2)],100),deferred_in_scope_ids:[]});
  await h.queue.refreshReviewScope();
  assert.equal(h.$('#review-session-progress').textContent,'待复核 100 张 · 稍后 2 张');
});
