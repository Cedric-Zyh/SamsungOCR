const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const workbench = require(path.join(root, 'static/workbench.js'));
const moduleNames = ['records', 'imports', 'recognition_plan', 'review', 'review_evidence', 'review_queue', 'progress', 'report', 'navigation'];
const modules = Object.fromEntries(moduleNames.map(name => [name, require(path.join(root, `static/modules/${name}.mjs`))]));
const helpers = require(path.join(root, 'static/modules/ui.mjs'));
const {createState} = require(path.join(root, 'static/modules/state.mjs'));
const {createApplication, browserEnvironment} = require(path.join(root, 'static/modules/application.mjs'));
const {createApi} = require(path.join(root, 'static/modules/api.mjs'));

// Load production modules directly; replace only external dependencies and DOM.
function element() {
  const listeners = new Map(), classes = new Set();
  return {
    value:'', textContent:'', innerHTML:'', checked:false, disabled:false, inert:false,
    dataset:{}, formValues:{}, attributes:{}, style:{}, parentElement:{setAttribute(){}},
    classList:{add:name=>classes.add(name), remove:name=>classes.delete(name),
      contains:name=>classes.has(name), toggle:(name, enabled)=>enabled ? classes.add(name) : classes.delete(name)},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); },
    async dispatch(type, target = this) { for (const listener of listeners.get(type) || []) await listener({target, currentTarget:this}); },
    focus() {}, replaceChildren() {}, scrollTo() {}, append() {},
    listenerCount() { return [...listeners.values()].reduce((count, rows)=>count+rows.length,0); },
    content:{cloneNode:()=>({})},
  };
}

function harness(names = []) {
  const nodes = new Map(), requests = [], notices = [], timers = [];
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  let hash = '#review/1';
  const location = {get hash() { return hash; }, set hash(value) { hash = value && !value.startsWith('#') ? `#${value}` : value; }};
  const bags = createState();
  bags.review.current = {id:1}; bags.review.reviewIntent = 0;
  const state = new Proxy({}, {
    get(_target, key) { const bag = Object.values(bags).find(bag => key in bag); return bag?.[key]; },
    set(_target, key, value) {
      const owner = key.startsWith('record') || ['records','selected','batchFilter'].includes(key) ? 'records'
        : key.startsWith('review') || ['current','artifactTab','queueRequest'].includes(key) ? 'review'
        : key.startsWith('report') ? 'report'
        : key.startsWith('poll') || key === 'restoreScroll' ? 'navigation'
        : key === 'resultRevision' ? 'results'
        : key.startsWith('daily') || key.startsWith('queue') || key.startsWith('work') || key === 'progressRecordCache' ? 'progress' : 'imports';
      bags[owner][key] = value; return true;
    },
  });
  const context = {
    state,$,$$:()=>[],URLSearchParams,AbortController,Date,Set,Map,console,
    ReceiptWorkbench:workbench,ReceiptQueue:require('../static/queue_state.js'),ReceiptImport:require('../static/import_files.js'),fieldSchema:{printed:[],handwritten:[],output:[]},planLabels:{},
    document:{...element(),body:{dataset:{activePage:'review'}},hidden:false},
    window:{...element(),confirm:()=>false,scrollY:0},location,localStorage:{getItem:()=>null,setItem:()=>{}},
    history:{replaceState:(_a,_b,hash)=>{context.location.hash=hash;}},
    toast:(message)=>notices.push(message),syncColumnFilters:()=>{},closeTablePopover:()=>{},
    FormData:class { constructor(form) { this.values = Object.entries(form.formValues); } [Symbol.iterator]() { return this.values[Symbol.iterator](); } },
    setTimeout:(callback,delay)=>{timers.push({callback,delay});return timers.length;},clearTimeout:()=>{},
    api:async (url, options)=>{requests.push({url,options});throw Error(`Unexpected request ${url}`);},
    renderProductTable:()=>{},setupDateConfirmation:()=>{},setupSealConfirmation:()=>{},setReviewImage:()=>{},focusReviewImage:()=>{},renderArtifacts:()=>{},
    renderHistory:async()=>{},renderGroundTruth:async()=>{},loadReviewQueue:async()=>{},continueReview:async()=>{},refreshVisibleResults:async()=>{},
    startReviewScope:async()=>{},getReviewScope:()=>({kind:'day',filters:{}}),
    ensureReviewScope:()=>{},
    markReviewSaved:(id,status)=>{if(status==='待复核')bags.review.reviewDeferred.add(id);else bags.review.reviewDeferred.delete(id);},
    showPage:page=>{context.document.body.dataset.activePage=page;},
  };
  const callbacks = new Proxy({}, {get:(_target, name) => (...args) => {
    if (typeof context[name] !== 'function') throw Error(`Missing dependency ${String(name)}`);
    return context[name](...args);
  }});
  const controllers = {};
  const parameters = {environment:context, ui:{$,$$:selector=>context.$$(selector),toast:message=>context.toast(message)},
    ...Object.fromEntries(Object.entries(bags).map(([key,value])=>[`${key}State`,value])),
    ReceiptWorkbench:workbench,ReceiptQueue:require('../static/queue_state.js'),ReceiptImport:require('../static/import_files.js'),fieldSchema:context.fieldSchema};
  const dependencies = new Proxy(parameters, {get:(target,key)=>key in target ? target[key] : callbacks[key]});
  for (const [name, exported] of Object.entries(modules)) controllers[name] = Object.values(exported)[0](dependencies);
  Object.assign(context, helpers);
  for (const name of names) {
    const controller = Object.values(controllers).find(controller=>typeof controller[name]==='function');
    if (controller) context[name] = controller[name];
    else if (name !== 'pollQueue') assert.equal(typeof context[name], 'function', `Missing production export: ${name}`);
  }
  if (names.includes('loadRecords')) controllers.records.initialize();
  if (names.includes('pollQueue')) {
    const app=createApplication(context,{state:bags,ui:parameters.ui,fieldSchema:context.fieldSchema,api:callbacks.api});
    app.progress.loadDailyResults=callbacks.loadDailyResults;
    app.records.loadRecords=callbacks.loadRecords;
    app.report.loadReport=callbacks.loadReport;
    context.pollQueue=app.pollQueue;
  }
  return {context,state,$,requests,notices,timers,bags,controllers,nodes};
}

function defer() {
  let resolve, reject;
  const promise = new Promise((a,b)=>{resolve=a;reject=b;});
  return {promise,resolve,reject};
}
const row = id => ({id,filename:`${id}.jpg`,fields:{},date_check:{},seal_check:{},review_status:'待复核'});
const page = (items,total=items.length,number=1) => ({items,total,page:number,page_size:100});
const recordFunctions = ['loadRecords','updateSelectionToolbar','toggleSelected','selectCurrentPage','recordRow','filenameParts','statusPill','statusClass','escapeHtml'];
const reviewFunctions = ['canLeaveReview','setReviewDirty','openReview','filenameParts','fieldEditor','percent','statusPill','statusClass','escapeHtml'];

function renderedSamples() {
  const h = harness(['recordRow','filenameParts','statusPill','statusClass','percent','escapeHtml','renderProductTable','artifactCard']);
  const filename = 'a" onmouseover="alert(1)" data-x=".jpg';
  const product = '32" 显示器 & 配件';
  const customer = '客户" onfocus="alert(2)';
  const markup = h.context.recordRow({...row(1),filename,fields:{客户名称:customer}});
  h.context.renderProductTable({columns:['商品名称'],rows:[{values:{商品名称:product}}]}, {});
  return {filename,product,customer,markup,productMarkup:h.$('[data-product-table]').innerHTML,
    unsafeArtifact:h.context.artifactCard('图片','javascript:alert(1)'),
    artifact:h.context.artifactCard('测试"图片','/files/a" onerror="alert(3).jpg')};
}

if (process.argv.includes('--render-samples')) {
  process.stdout.write(JSON.stringify(renderedSamples()));
} else {
  test('application composition initializes listeners and the polling timer exactly once', async () => {
    const h=harness(); h.context.location.hash='#settings';
    const app=createApplication(h.context,{state:h.bags,ui:{$:h.$,$$:()=>[],toast:h.context.toast},fieldSchema:h.context.fieldSchema,api:h.context.api});
    assert.equal(h.timers.length,0);assert.equal(h.requests.length,0);
    app.initialize();
    const count=()=>h.context.document.listenerCount()+h.context.window.listenerCount()+[...h.nodes.values()].reduce((sum,node)=>sum+node.listenerCount(),0);
    const listeners=count(), timers=h.timers.length;
    assert(listeners>0);assert.equal(h.context.document.body.dataset.activePage,'settings');
    app.initialize();assert.equal(count(),listeners);assert.equal(h.timers.length,timers);
    assert.equal(h.requests.length,0);assert.equal(h.timers.at(-1).delay,2000);
  });

  test('browser environment uses explicitly supplied legacy helpers and tolerates unavailable storage until access', () => {
    const h=harness(); h.context.window.fetch=()=>{}; h.context.window.setTimeout=h.context.setTimeout; h.context.window.clearTimeout=h.context.clearTimeout;
    Object.defineProperty(h.context.window,'localStorage',{get:()=>{throw Error('storage disabled');}});
    const environment=browserEnvironment(h.context.window,{ReceiptWorkbench:workbench});
    assert.equal(environment.ReceiptWorkbench,workbench);
    assert.throws(()=>environment.localStorage.getItem('plan'),/storage disabled/);
  });

  test('composed progress controller receives its queue and workbench helpers', async () => {
    const h=harness(), requests=[];
    const app=createApplication(h.context,{state:h.bags,ui:{$:h.$,$$:()=>[],toast:h.context.toast},fieldSchema:h.context.fieldSchema,api:async url=>{
      requests.push(url);
      if(url==='/api/queue/control')return {paused:false};
      if(url.startsWith('/api/queue?'))return [];
      if(url.startsWith('/api/daily-results?'))return [row(1)];
      throw Error(url);
    }});
    await app.progress.loadDailyResults();
    assert.equal(app.state.progress.workItems.length,1);
    assert.equal(h.$('#workbench-total-label').textContent,'当天共 1 张');
    assert.equal(requests.length,3);
  });

  test('review entry changes scope on navigation and ignores late workbench responses', async () => {
    const h=harness(), pending=defer();
    h.$('#progress-date').value='2025-03-31';
    h.controllers.navigation.showPage('progress');
    h.controllers.progress.renderTask({total:108,completed:108,status:'已完成',pending_review:108});
    assert.match(h.$('#open-batch-review').attributes['aria-label'],/108 张待复核/);
    h.context.api=async url=>url.startsWith('/api/queue?')?[]:url==='/api/queue/control'?{paused:false}:pending.promise;
    const loading=h.controllers.progress.loadDailyResults();
    h.controllers.navigation.showPage('records');
    assert.equal(h.$('#open-batch-review').attributes['aria-label'],'人工复核当前筛选');
    assert.equal(h.$('#open-batch-review').title,'复核当前筛选范围内的回单');
    assert.equal(h.$('#review-entry-count').classList.contains('hidden'),true);
    pending.resolve([row(1)]);await loading;
    assert.equal(h.$('#open-batch-review').attributes['aria-label'],'人工复核当前筛选');
    assert.equal(h.$('#review-entry-count').textContent,'');
    h.controllers.navigation.showPage('quality');
    assert.equal(h.$('#open-batch-review').attributes['aria-label'],'人工复核，2025-03-31，当天全部');
    assert.match(h.$('#open-batch-review').title,/2025-03-31/);
  });

  test('changing the workbench date clears the old count before its new request finishes', async () => {
    const h=harness(), first=defer(), second=defer();
    h.$('#progress-date').value='2025-03-31';
    h.controllers.navigation.showPage('progress');
    h.controllers.progress.renderTask({total:108,completed:108,status:'已完成',pending_review:108});
    h.context.api=async url=>url.startsWith('/api/queue?')?[]:url==='/api/queue/control'?{paused:false}:url.includes('2025-04-01')?first.promise:second.promise;
    h.$('#progress-date').value='2025-04-01';
    const older=h.controllers.progress.loadDailyResults();
    assert.equal(h.$('#open-batch-review').attributes['aria-label'],'人工复核，2025-04-01，当天全部');
    assert.equal(h.$('#review-entry-count').classList.contains('hidden'),true);
    h.$('#progress-date').value='2025-04-02';
    const newer=h.controllers.progress.loadDailyResults();
    first.resolve([row(1)]);await older;
    assert.equal(h.$('#open-batch-review').attributes['aria-label'],'人工复核，2025-04-02，当天全部');
    second.resolve([]);await newer;
    assert.equal(h.$('#open-batch-review').attributes['aria-label'],'人工复核，2025-04-02，0 张待复核');
  });

  test('workbench completion distinguishes processed receipts from human review', async () => {
    const h=harness(); h.$('#progress-date').value='2026-09-10';
    h.context.api=async url=>url.startsWith('/api/queue?')?[]:url==='/api/queue/control'?{paused:false}:[row(1)];
    await h.controllers.progress.loadDailyResults();
    assert.equal(h.$('#task-summary').textContent,'已处理 1 / 1 张 · 识别已结束');
    assert.equal(h.$('#task-summary').dataset.status,'review');
    assert.equal(h.$('#progress-note').textContent,'');
    assert.equal(h.$('#workbench-visible-count').textContent,'待复核 1 张');
    h.controllers.progress.renderTask({total:0,completed:0,pending_review:0});
    assert.equal(h.$('#task-summary').textContent,'暂无回单');
  });

  test('workbench loading failures identify stale data and hide records from a previous day', async () => {
    const h=harness(); h.$('#progress-date').value='2026-09-10';
    h.context.api=async url=>url.startsWith('/api/queue?')?[]:url==='/api/queue/control'?{paused:false}:[row(1)];
    await h.controllers.progress.loadDailyResults();
    h.context.api=async()=>{throw Error('offline');};
    await assert.rejects(h.controllers.progress.loadDailyResults(),/offline/);
    assert.equal(h.$('#task-section').classList.contains('hidden'),false);
    assert.match(h.$('#progress-note').textContent,/上次成功加载/);
    assert.equal(h.$('#refresh-progress').textContent,'重试');
    h.$('#progress-date').value='2026-09-09';
    await assert.rejects(h.controllers.progress.loadDailyResults(),/offline/);
    assert.equal(h.$('#task-section').classList.contains('hidden'),true);
    assert.match(h.$('#progress-note').textContent,/2026-09-09 的回单加载失败/);
    assert.equal(h.$('#task-section').attributes['aria-busy'],undefined);
  });

  test('workbench tabs keep selection and list count synchronized', async () => {
    const h=harness(); const metric=element(), filter=element();
    metric.dataset.workFilter='review'; filter.dataset.workFilter='processing';
    h.context.$$=selector=>selector==='[data-work-filter]'?[metric,filter]:[];
    let scroll=0; h.$('.workbench-table-wrap').scrollTo=()=>scroll++;
    h.bags.progress.workItems=[{id:1,status:'succeeded',record:row(1)}];
    h.controllers.progress.initialize();
    await metric.dispatch('click');
    assert.equal(h.bags.progress.workFilter,'review');
    assert.equal(metric.attributes['aria-pressed'],'true');
    assert.equal(filter.attributes['aria-pressed'],'false');
    assert.equal(scroll,1);
    assert.match(h.$('#workbench-visible-count').textContent,/待复核 1 张/);
  });

  test('API writes invalidate cached results only after success and preserve conflict details', async () => {
    const responses=[{ok:true,status:200,body:{id:1}},{ok:false,status:409,body:{error:'updated',code:'review_revision_conflict'}}];
    const requests=[];let invalidations=0;
    const api=createApi({fetch:async (url,options)=>{requests.push({url,options});const response=responses.shift();return {...response,headers:{get:()=> 'application/json'},json:async()=>response.body};},onResultsChanged:()=>{invalidations++;}});
    await api('/api/results/1/review',{method:'PATCH',json:{review_revision:'initial'}});
    assert.equal(JSON.parse(requests[0].options.body).review_revision,'initial');
    await assert.rejects(api('/api/results/1/review',{method:'PATCH',json:{}}),error=>error.status===409&&error.code==='review_revision_conflict');
    assert.equal(invalidations,1);
  });

  test('background refresh retains current selection and edits made while its request is pending', async () => {
    const h = harness(recordFunctions);
    h.context.api = async()=>page([row(1),row(2)],1500);
    await h.context.loadRecords();
    h.context.toggleSelected(1,true);
    const pending = defer();
    h.context.api = ()=>pending.promise;
    const refresh = h.context.loadRecords({background:true});
    assert.equal(h.$('#records-body').inert,false);
    h.context.toggleSelected(2,true);
    pending.resolve(page([row(2),row(3)],1500));
    await refresh;
    assert.deepEqual([...h.state.selected],[2]);
    assert.equal(h.$('#select-all').indeterminate,true);
    assert.equal(h.$('#record-count').textContent,'共 1500 张');
    assert.equal(h.$('#records-page-next').disabled,false);
  });

  test('failed refresh and input composition retain the existing rows and selection', async () => {
    const h = harness(recordFunctions);
    h.context.api = async()=>page([row(1)]);
    await h.context.loadRecords(); h.context.toggleSelected(1,true);
    const markup = h.$('#records-body').innerHTML;
    let calls = 0;
    h.context.api = async()=>{calls++;throw Error('offline');};
    await h.context.loadRecords({background:true});
    assert.equal(calls,1);
    assert.deepEqual([...h.state.selected],[1]);
    assert.equal(h.$('#records-body').innerHTML,markup);
    assert.equal(h.$('#records-body').inert,false);
    await h.context.document.dispatch('compositionstart', {form:h.$('#filters'),type:'search'});
    await h.context.loadRecords({background:true});
    assert.equal(calls,1);
    await h.context.document.dispatch('compositionend', {form:h.$('#filters'),type:'search'});
    await h.timers.at(-1).callback();
    assert.equal(calls,2);
    assert.equal(h.$('#records-body').inert,false);
  });

  test('select-all applies to every record on the page before synchronizing its checkbox', async () => {
    const h=harness(recordFunctions);
    h.context.api=async()=>page([row(1),row(2),row(3)]);
    await h.context.loadRecords();
    h.context.selectCurrentPage(true);
    assert.deepEqual([...h.state.selected],[1,2,3]);
    assert.equal(h.$('#select-all').checked,true);
    assert.equal(h.$('#select-all').indeterminate,false);
    h.context.selectCurrentPage(false);
    assert.equal(h.state.selected.size,0);assert.equal(h.$('#select-all').checked,false);
  });

  test('record pagination reaches beyond 1000 and changing filters returns to page one', async () => {
    const h = harness(recordFunctions), pages=[];
    h.context.api = async url=>{const n=Number(new URL(url,'http://local').searchParams.get('page'));pages.push(n);return page([row(n*100)],1500,n);};
    await h.context.loadRecords();
    h.context.toggleSelected(100,true);
    await h.context.loadRecords({page:11});
    assert.equal(h.state.records[0].id,1100);
    assert.equal(h.state.selected.size,0);
    assert.equal(h.$('#records-page-info').textContent,'第 11 / 15 页 · 本页 1 张');
    h.$('#filters').formValues.customer='新客户';
    await h.context.loadRecords();
    assert.deepEqual(pages,[1,11,1]);
  });

  test('review entry snapshots all current filters and selected IDs independently of the visible page', async () => {
    const h = harness([...recordFunctions, 'getReviewScope', 'startRecordsReview']);
    h.$('#filters').formValues = {import_date:'2026-09-10',customer:'甲客户',search:'invoice',date_status:'匹配待确认',text_match:'prefix'};
    h.context.api = async()=>page([row(1),row(2)],201);
    await h.context.loadRecords();
    let opened;
    h.context.startReviewScope = async(scope, options)=>{opened={scope, options};};
    await h.context.startRecordsReview('filtered',1);
    assert.equal(opened.scope.filters.customer,'甲客户');
    assert.equal(opened.scope.filters.search,'invoice');
    assert.equal(opened.scope.filters.date_status,'匹配待确认');
    assert.equal(opened.options.recordId,1);
    assert.equal(opened.scope.filters.page,undefined);
    h.context.toggleSelected(2,true);
    await h.context.startRecordsReview('selected');
    assert.deepEqual(opened.scope.ids,[2]);
    assert.deepEqual(opened.scope.filters,{});
    await h.context.startRecordsReview('day');
    assert.deepEqual(opened.scope.filters,{import_date:'2026-09-10'});
  });

  test('filter chips escape values and removing one leaves the other filters intact', async () => {
    const h = harness([...recordFunctions,'removeFilter','renderFilterChips']);
    const fields={customer:h.$('[name=customer]'),seal_status:h.$('[name=seal_status]')};
    fields.customer.value='甲"<客户>';fields.seal_status.value='匹配待确认';
    h.$('#filters').formValues={customer:fields.customer.value,seal_status:fields.seal_status.value};
    h.$('#filters').elements={namedItem:name=>fields[name]};
    h.context.renderFilterChips();
    assert(h.$('#active-filters').innerHTML.includes('甲&quot;&lt;客户&gt;'));
    assert(h.$('#active-filters').innerHTML.includes('data-remove-filter="seal_status"'));
    h.context.removeFilter('customer');
    assert.equal(fields.customer.value,'');assert.equal(fields.seal_status.value,'匹配待确认');
    assert.equal(h.state.recordPage,1);
  });

  test('density preference applies to the existing table and persists without changing records', () => {
    const h=harness(['setDensity']);const writes=[];
    h.context.localStorage.setItem=(key,value)=>writes.push([key,value]);
    h.context.setDensity(true);
    assert.equal(h.$('.records-section').classList.contains('is-compact'),true);
    assert.equal(h.$('#records-density-toggle').attributes['aria-pressed'],'true');
    assert.deepEqual(writes,[['receipt.records.compact','true']]);
    h.context.setDensity(false);
    assert.equal(h.$('.records-section').classList.contains('is-compact'),false);
  });

  test('a background refresh clamps a removed last page and still completes', async () => {
    const h = harness(recordFunctions), pages=[];
    h.state.recordFilterKey=''; h.state.recordPage=2;
    h.context.api=async url=>{const n=Number(new URL(url,'http://local').searchParams.get('page'));pages.push(n);return page(n===1?[row(1)]:[],100,n);};
    await h.context.loadRecords({background:true});
    assert.deepEqual(pages,[2,1]);
    assert.equal(h.state.recordPage,1);
    assert.equal(h.state.recordLoading,false);
  });

  test('a later foreground filter response cannot be replaced by an older background response', async () => {
    const h = harness(recordFunctions), first=defer(), second=defer();
    h.state.recordFilterKey='';
    let calls=0; h.context.api=()=>++calls===1?first.promise:second.promise;
    const old=h.context.loadRecords({background:true});
    h.$('#filters').formValues.customer='新客户';
    const fresh=h.context.loadRecords();
    second.resolve(page([row(2)])); await fresh;
    first.resolve(page([row(1)])); await old;
    assert.equal(h.state.records[0].id,2);
  });

  test('detail navigation retains edits made while waiting and restores the matching route', async () => {
    const h = harness(reviewFunctions), pending=defer();
    h.context.api=()=>pending.promise;
    h.context.location.hash='#review/2';
    const opening=h.context.openReview(2);
    h.context.setReviewDirty();
    pending.resolve(row(2)); await opening;
    assert.equal(h.state.current.id,1);
    assert.equal(h.state.reviewDirty,true);
    assert.equal(h.context.location.hash,'#review/1');
    assert.equal(h.notices.length,1);
  });

  test('failed navigation does not discard preexisting unsaved edits after confirmation', async () => {
    const h = harness(reviewFunctions);
    h.state.reviewDirty=true;h.context.window.confirm=()=>true;
    h.context.api=async()=>{throw Error('offline');};
    await assert.rejects(h.context.openReview(2),/offline/);
    assert.equal(h.state.current.id,1);assert.equal(h.state.reviewDirty,true);
  });

  test('only the latest detail request may replace the review form', async () => {
    const h=harness(reviewFunctions), first=defer(),second=defer();
    let calls=0; h.context.api=()=>++calls===1?first.promise:second.promise;
    const old=h.context.openReview(2),fresh=h.context.openReview(3);
    second.resolve(row(3));await fresh;
    first.resolve(row(2));await old;
    assert.equal(h.state.current.id,3);assert.equal(h.context.location.hash,'#review/3');
  });

  test('saving the current review invalidates any detail navigation still in flight', async () => {
    const h=harness([...reviewFunctions,'submitReview']), pending=defer(),form=h.$('#review-form');
    for(const name of ['actual_date','actual_date_confirmed','seal_text','seal_confirmed_match','human_note','error_type','save_ground_truth','truth_seal_should_match']) form[name]=h.$(`[name=${name}]`);
    h.context.api=(_url,options)=>options?.method==='PATCH'?Promise.resolve(row(1)):pending.promise;
    const opening=h.context.openReview(2);
    await h.context.submitReview('待复核','需人工复核');
    pending.resolve(row(2));await opening;
    assert.equal(h.state.current.id,1);assert.equal(h.context.location.hash,'#review/1');
  });

  test('saving a note sends no date confirmation, while the explicit date choice does', async () => {
    const h=harness(['setupDateConfirmation','setReviewDirty','submitReview','statusPill','statusClass','escapeHtml']);
    const form=h.$('#review-form');
    for(const name of ['actual_date','actual_date_confirmed','seal_text','seal_confirmed_match','human_note','error_type','save_ground_truth','truth_seal_should_match']) form[name]=h.$(`[name=${name}]`);
    form.seal_text.value='客户收货章';form.human_note.value='只补充备注';
    h.context.setupDateConfirmation({...row(1),fields:{要求到货:'2025-03-31'},date_check:{actual:'2025-03-31',status:'匹配',reliable:false,source:'vision'}},{});
    h.state.current.review_revision='original-revision';
    const payloads=[];
    h.context.api=async (_url, options)=>{payloads.push(options.json);return {...row(1),review_revision:'saved-revision'};};
    await h.context.submitReview('待复核','需人工复核');
    assert.equal(payloads[0].review_revision,'original-revision');
    assert.equal(h.state.current.review_revision,'saved-revision');
    assert.equal(payloads[0].actual_date,'2025-03-31');
    assert.equal(payloads[0].actual_date_confirmed,false);
    assert.equal(payloads[0].seal_confirmed_match,null);
    await h.$('[data-date-choice=same]').dispatch('click');
    await h.context.submitReview('待复核','需人工复核');
    assert.equal(payloads[1].actual_date_confirmed,true);
    assert.equal(payloads[1].review_revision,'saved-revision');
  });

  test('revision conflicts preserve the current edits and explain how to reopen the latest record', async () => {
    const h=harness(['submitReview']), form=h.$('#review-form');
    for(const name of ['actual_date','actual_date_confirmed','seal_text','seal_confirmed_match','human_note','error_type','save_ground_truth','truth_seal_should_match']) form[name]=h.$(`[name=${name}]`);
    form.human_note.value='尚未保存的新备注';
    h.state.current.review_revision='old-revision';h.state.reviewDirty=true;
    h.context.api=async (_url,options)=>{
      assert.equal(options.json.review_revision,'old-revision');
      throw Object.assign(Error('回单已由其他窗口更新'),{status:409,code:'review_revision_conflict'});
    };
    await h.context.submitReview('待复核','需人工复核');
    assert.equal(h.state.reviewDirty,true);assert.equal(h.state.reviewSaving,false);
    assert.equal(h.state.current.review_revision,'old-revision');
    assert.equal(form.human_note.value,'尚未保存的新备注');
    assert.equal(h.notices.at(-1).includes('请重新打开回单'),true);
  });

  test('all-days review advances beyond 2000 deferred records through server pages', async () => {
    const h=harness(['loadReviewQueue','openNextReview','escapeHtml']), pages=[];
    h.state.reviewDeferred=new Set(Array.from({length:2000},(_,i)=>i+1));
    let opened;
    h.context.openReview=async id=>{opened=id;};
    h.context.api=async url=>{
      const parsed=new URL(url,'http://local');
      assert.equal(parsed.pathname,'/api/results');assert.equal(parsed.searchParams.get('reviewable'),'1');
      const n=Number(parsed.searchParams.get('page'));pages.push(n);
      const items=Array.from({length:Math.min(100,2001-(n-1)*100)},(_,i)=>row((n-1)*100+i+1));
      return page(items,2001,n);
    };
    await h.context.loadReviewQueue(true);
    assert.equal(opened,2001);assert.equal(pages.length,21);
    assert.equal(h.$('#review-scope').textContent,'2001 张待复核');
    assert.equal(h.$('#review-page-next').disabled,true);
  });

  test('date review paging preserves queue-day scope and a cancelled advance stays cancelled', async () => {
    const h=harness(['loadReviewQueue','openNextReview','escapeHtml']);
    h.state.reviewDay='2025-03-31';
    h.context.api=async url=>{
      const parsed=new URL(url,'http://local');
      assert.equal(parsed.pathname,'/api/daily-results');
      assert.equal(parsed.searchParams.get('include_queue'),'1');
      assert.equal(parsed.searchParams.get('import_date'),'2025-03-31');
      return page([row(1)],101);
    };
    const first=await h.context.loadReviewQueue(false);
    h.state.reviewDeferred.add(1);
    const pending=defer();h.context.api=()=>pending.promise;
    let opened=false;h.context.openReview=()=>{opened=true;};
    const advancing=h.context.openNextReview(first);
    h.state.reviewIntent++;
    pending.resolve(page([row(101)],101,2));await advancing;
    assert.equal(opened,false);
  });

  test('review ends only after all pages have been checked for non-deferred records', async () => {
    const h=harness(['loadReviewQueue','openNextReview','escapeHtml']), pages=[];
    h.state.reviewDeferred=new Set(Array.from({length:201},(_,i)=>i+1));
    let ended=0;h.context.showReviewEmpty=()=>{ended++;};
    h.context.api=async url=>{
      const n=Number(new URL(url,'http://local').searchParams.get('page'));pages.push(n);
      return page(Array.from({length:Math.min(100,201-(n-1)*100)},(_,i)=>row((n-1)*100+i+1)),201,n);
    };
    await h.context.loadReviewQueue(true);
    assert.deepEqual(pages,[1,2,3]);assert.equal(ended,1);
    assert.equal(h.context.location.hash,'#review');
  });

  test('polling only refreshes its visible page and pauses in hidden tabs', async () => {
    const h=harness(['pollQueue']), calls=[];
    h.state.recordsLoaded=true;
    h.context.loadDailyResults=async()=>{calls.push('progress');h.state.dailyAttemptAt=Date.now();};
    h.context.loadRecords=async options=>{assert.equal(options.background,true);calls.push('records');h.state.recordAttemptAt=Date.now();};
    h.context.loadReport=async()=>{calls.push('quality');h.state.reportAttemptAt=Date.now();};
    for(const activePage of ['settings','review','records','records','quality','quality','progress','progress']) {
      h.context.document.body.dataset.activePage=activePage;await h.context.pollQueue();
    }
    assert.deepEqual(calls,['records','quality','progress']);
    h.context.document.hidden=true;h.state.dailyAttemptAt=0;await h.context.pollQueue();
    assert.equal(calls.length,3);assert.equal(h.timers.at(-1).delay,5000);
  });

  test('statistics refreshes coalesce and identify fields missing ground truth', async () => {
    const h=harness(['loadReport','renderReport','percent','escapeHtml']), pending=defer();
    let calls=0;h.context.api=()=>{calls++;return pending.promise;};
    const first=h.context.loadReport(),second=h.context.loadReport();
    assert.equal(first,second);assert.equal(calls,1);
    pending.resolve({accuracy:{field_accuracy:1,field_coverage:{unlabeled_fields:['签章要求','仓库接收人']}}});
    await first;
    assert.equal(h.$('#accuracy-scope').textContent.includes('2 项字段缺少真值'),true);
    assert.equal(h.$('#accuracy-scope').title.includes('签章要求、仓库接收人'),true);
    assert.equal(h.state.reportPromise,null);
  });
}
