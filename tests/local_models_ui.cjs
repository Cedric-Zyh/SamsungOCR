const test = require('node:test');
const assert = require('node:assert/strict');
const {createLocalModelSettings} = require('../static/modules/settings/local_models.mjs');

const unloaded = () => ({state:'unloaded',loaded:false,ready:false,busy:false,operation:'',error:'',
  required_count:2,loaded_count:0,initialization_seconds:null,
  models:[{id:'page',label:'整页文字识别',required:true,loaded:false}]});
const ready = () => ({...unloaded(),state:'ready',loaded:true,ready:true,loaded_count:2,
  initialization_seconds:5.96,models:[{id:'page',label:'整页文字识别',required:true,loaded:true}]});

function harness({status=unloaded(),post,flush,hash='#settings'}={}) {
  const nodes = new Map(), timers = new Map(), calls = [], events = new Map();let timerId=0;
  function node() {
    const handlers={};return {textContent:'',innerHTML:'',dataset:{},hidden:false,disabled:false,checked:false,
      addEventListener:(event,fn)=>handlers[event]=fn,
      fire:(event)=>handlers[event]?.({target:nodes.get('[data-local-model-toggle]')})};
  }
  for(const key of ['toggle','state','description','note','prepare','list','duration','feedback']) nodes.set(`[data-local-model-${key}]`,node());
  const root={querySelector:selector=>nodes.get(selector)};
  const environment={document:{hidden:false,addEventListener:(name,fn)=>events.set(`doc:${name}`,fn)},
    window:{location:{hash},addEventListener:(name,fn)=>events.set(name,fn)},
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id)};
  const service={localModels:async()=>{calls.push('GET');return status;},
    setLocalModels:async value=>{calls.push(['POST',value]);return post?post(value):{...status,state:value?'loading':'unloading',operation:value?'loading':'unloading'};}};
  const controller=createLocalModelSettings({environment,ui:{$:()=>root},services:{settings:service},
    flushSettings:async()=>{calls.push('flush');if(flush)await flush();}});
  controller.initialize();
  return {nodes,calls,controller,environment,timers,events,service,
    tick:async()=>{await Promise.resolve();await Promise.resolve();},
    setStatus:value=>status=value,
    toggle:async value=>{nodes.get('[data-local-model-toggle]').checked=value;await nodes.get('[data-local-model-toggle]').fire('change');}};
}

test('status reads do not load models and all controls reflect actual readiness',async()=>{
  const h=harness();await h.tick();
  assert.deepEqual(h.calls,['GET']);assert(!h.nodes.get('[data-local-model-toggle]').checked);
  assert.equal(h.nodes.get('[data-local-model-state]').textContent,'未加载');
  h.setStatus(ready());await h.controller.load();
  assert(h.nodes.get('[data-local-model-toggle]').checked);
  assert.equal(h.nodes.get('[data-local-model-state]').textContent,'已就绪');
  assert.match(h.nodes.get('[data-local-model-duration]').textContent,/5.96 秒/);
});

test('one click flushes the latest plan and loads, while the live operation locks repeated clicks',async()=>{
  const h=harness();await h.tick();await h.toggle(true);
  assert.deepEqual(h.calls,['GET','flush',['POST',true]]);
  assert(h.nodes.get('[data-local-model-toggle]').disabled);
  assert.equal(h.nodes.get('[data-local-model-state]').textContent,'正在初始化…');
  await h.toggle(true);assert.equal(h.calls.filter(x=>Array.isArray(x)).length,1);
  h.setStatus(ready());await h.controller.load();await h.toggle(false);
  assert.deepEqual(h.calls.at(-1),['POST',false]);
  assert.equal(h.nodes.get('[data-local-model-state]').textContent,'正在释放…');
});

test('busy receipts lock release and remote-only plans do not offer unnecessary initialization',async()=>{
  const h=harness({status:{...ready(),state:'busy',busy:true}});await h.tick();
  assert(h.nodes.get('[data-local-model-toggle]').disabled);
  await h.toggle(false);assert.deepEqual(h.calls,['GET']);
  assert.match(h.nodes.get('[data-local-model-note]').textContent,/完成任务后/);
  h.setStatus({...unloaded(),state:'unneeded',required_count:0,models:[]});await h.controller.load();
  assert(h.nodes.get('[data-local-model-toggle]').disabled);
  assert.equal(h.nodes.get('[data-local-model-state]').textContent,'无需加载');
});

test('failed settings save never starts a load and partial models can be completed with one click',async()=>{
  const h=harness({flush:async()=>{throw Error('设置未保存');}});await h.tick();await h.toggle(true);
  assert(!h.calls.some(x=>Array.isArray(x)));
  assert.match(h.nodes.get('[data-local-model-feedback]').textContent,/设置未保存/);
  assert(!h.nodes.get('[data-local-model-toggle]').checked);
  const partial=harness({status:{...ready(),state:'partial',ready:false}});await partial.tick();
  assert(!partial.nodes.get('[data-local-model-prepare]').hidden);
  await partial.nodes.get('[data-local-model-prepare]').fire('click');
  assert.deepEqual(partial.calls.at(-1),['POST',true]);
});

test('server conflicts restore the real busy state and dynamic labels are escaped',async()=>{
  const busy={...ready(),state:'busy',busy:true};
  const h=harness({post:async()=>{throw Object.assign(Error('正在识别'),{status:409,details:busy});}});
  await h.tick();await h.toggle(true);
  assert(h.nodes.get('[data-local-model-toggle]').disabled);
  assert(h.nodes.get('[data-local-model-toggle]').checked);
  assert.match(h.nodes.get('[data-local-model-feedback]').textContent,/正在识别/);
  h.setStatus({...ready(),models:[{label:'<img src=x>',required:true,loaded:true}]});await h.controller.load();
  assert.match(h.nodes.get('[data-local-model-list]').innerHTML,/&lt;img src=x&gt;/);
});

test('leaving settings stops polling',async()=>{
  const h=harness({hash:'#records'});await h.tick();assert.deepEqual(h.calls,[]);assert.equal(h.timers.size,0);
  h.environment.window.location.hash='#settings';h.events.get('hashchange')();await h.tick();assert.equal(h.calls.length,1);
  h.environment.document.hidden=true;h.events.get('doc:visibilitychange')();assert.equal(h.timers.size,0);
});

test('a late pre-click read cannot replace the current loading state',async()=>{
  const h=harness();await h.tick();let finishRead;
  h.service.localModels=()=>new Promise(resolve=>{finishRead=resolve;});
  const pending=h.controller.load();
  await h.toggle(true);
  finishRead(ready());await pending;
  assert.equal(h.nodes.get('[data-local-model-state]').textContent,'正在初始化…');
  assert(h.nodes.get('[data-local-model-toggle]').disabled);
});
