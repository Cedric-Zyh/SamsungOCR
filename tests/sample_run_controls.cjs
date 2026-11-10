const test = require('node:test');
const assert = require('node:assert/strict');
const {createSampleExecutionMenu} = require('../static/modules/samples/execution_menu.mjs');
const {createSampleRunProgress} = require('../static/modules/samples/run_progress.mjs');

function harness() {
  const nodes = new Map(), storage = new Map(), doc = {activeElement:null,handlers:{},
    addEventListener(name, fn) { this.handlers[name] = fn; }};
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {textContent:'',innerHTML:'',hidden:true,dataset:{},attrs:{},handlers:{},
      setAttribute(key,value) { this.attrs[key] = value; },
      addEventListener(name,fn) { this.handlers[name] = fn; },
      focus() { doc.activeElement = this; }});
    return nodes.get(selector);
  };
  return {$, doc, environment:{document:doc,window:{localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)}}}};
}

test('mode popup selects with checkmarks, keyboard navigation, Escape and outside click', () => {
  const h = harness(), modes = {both:{label:'本地 + 单证通'},local:{label:'只本地'},qingtong:{label:'只清瞳'}};
  const options = Object.keys(modes).map(mode=>Object.assign(h.$(`[mode=${mode}]`),{dataset:{sampleMode:mode}}));
  const menu = createSampleExecutionMenu({environment:h.environment,ui:{$:h.$,$$:()=>options},modes,
    changed:value=>menu.render({value,disabled:false})});
  menu.initialize(); menu.render({value:'both',disabled:false});
  const trigger = h.$('#sample-execution-mode'), panel = h.$('#sample-execution-menu');
  trigger.handlers.keydown({key:'ArrowDown',preventDefault() {}});
  assert.equal(panel.hidden,false);
  assert.equal(h.doc.activeElement,options[0]);
  panel.handlers.keydown({key:'ArrowDown',preventDefault() {}});
  assert.equal(h.doc.activeElement,options[1]);
  panel.handlers.click({target:{closest:()=>options[1]}});
  assert.equal(h.$('#sample-execution-label').textContent,'只本地');
  assert.equal(options[1].attrs['aria-checked'],'true');
  assert.equal(options[0].attrs['aria-checked'],'false');
  assert.equal(panel.hidden,true);
  assert.equal(h.doc.activeElement,trigger);
  trigger.handlers.click();
  panel.handlers.keydown({key:'Escape',preventDefault() {}});
  assert.equal(panel.hidden,true);
  trigger.handlers.click();
  h.doc.handlers.click({target:{closest:()=>null}});
  assert.equal(panel.hidden,true);
  menu.render({value:'local',disabled:true});
  trigger.handlers.click();
  assert.equal(panel.hidden,true);
});

test('run feedback shows per-run progress, remote waiting, completion and survives reload', async () => {
  const h = harness(); let company='甲公司';
  let progress = {total:3,processed:1,completed:1,waiting:2,finished:false};
  const args = {environment:h.environment,ui:{$:h.$},currentCompany:()=>company,
    services:{samples:{progress:async taskId=>{assert.equal(taskId,'batch-A');return progress;}}}};
  const view = createSampleRunProgress(args);
  view.initialize(); view.render({submitting:false,hasRunnable:true});
  assert.match(h.$('#sample-run-company').innerHTML,/开始运行/);
  view.start('batch-A',3,company);
  await view.refresh();
  assert.match(h.$('#sample-run-company').innerHTML,/运行中 1\/3/);
  assert.equal(h.$('#sample-run-company').disabled,true);
  assert.equal(h.$('#sample-run-progress-note').textContent,'单证通待返回 2');
  const restored = createSampleRunProgress(args);
  restored.initialize(); restored.render({submitting:false,hasRunnable:true});
  assert.match(h.$('#sample-run-company').innerHTML,/运行中 1\/3/);
  company='乙公司'; view.render();
  assert.equal(h.$('#sample-run-progress').hidden,true);
  company='甲公司';
  progress = {total:3,processed:3,completed:3,finished:true};
  await view.refresh();
  assert.match(h.$('#sample-run-company').innerHTML,/开始运行/);
  assert.equal(h.$('#sample-run-company').disabled,false);
  assert.equal(h.$('#sample-run-progress-label').textContent,'已结束 3 / 3');
  assert.equal(h.$('#sample-run-progress-bar').value,3);
});

test('late progress from an older batch cannot replace a newly started batch', async () => {
  const h = harness(), pending = new Map();
  const view = createSampleRunProgress({environment:h.environment,ui:{$:h.$},currentCompany:()=>'',
    services:{samples:{progress:taskId=>new Promise(resolve=>pending.set(taskId,resolve))}}});
  view.render({submitting:false,hasRunnable:true});
  view.start('old',5,''); const old = view.refresh();
  view.start('new',2,''); const latest = view.refresh();
  pending.get('old')({total:5,processed:5,finished:true}); await old;
  assert.match(h.$('#sample-run-company').innerHTML,/运行中 0\/2/);
  pending.get('new')({total:2,processed:1,waiting:1,finished:false}); await latest;
  assert.match(h.$('#sample-run-company').innerHTML,/运行中 1\/2/);
});

test('progress connection errors keep the last count and missing runs clear the feedback', async () => {
  const h = harness(); let failure;
  const view = createSampleRunProgress({environment:h.environment,ui:{$:h.$},currentCompany:()=>'',
    services:{samples:{progress:async()=>{if(failure)throw failure;return {total:2,processed:1,finished:false};}}}});
  view.render({submitting:false,hasRunnable:true}); view.start('task',2,'');
  await view.refresh();
  failure = new Error('offline'); await view.refresh();
  assert.match(h.$('#sample-run-company').innerHTML,/运行中 1\/2/);
  assert.match(h.$('#sample-run-progress-note').textContent,/正在重试/);
  failure = Object.assign(new Error('deleted'),{status:404}); await view.refresh();
  assert.equal(h.$('#sample-run-progress').hidden,true);
  assert.match(h.$('#sample-run-company').innerHTML,/开始运行/);
});
