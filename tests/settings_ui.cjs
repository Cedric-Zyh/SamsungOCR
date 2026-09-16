const assert = require('node:assert/strict');
const test = require('node:test');
const {createRecognitionPlan} = require('../static/modules/recognition_plan.mjs');

const stages = ['fields', 'products', 'handwriting', 'date', 'seal'];
const emptyPlan = () => Object.fromEntries(stages.map(stage => [stage, []]));
const sealTest = () => ({...emptyPlan(), fields:['vision'], seal:['qingtong']});

function node(dataset = {}) {
  const classes = new Set(), listeners = new Map();
  return {
    dataset, checked:false, disabled:false, value:'', textContent:'Nested title and longer description', innerHTML:'', title:'', attributes:{},
    classList:{toggle(name, active) { if (active) classes.add(name); else classes.delete(name); }, contains:name => classes.has(name)},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(event, callback) { listeners.set(event, callback); },
    fire(event) { listeners.get(event)?.({target:this}); },
  };
}
function harness({saved = null, providers = ['vision', 'paddle', 'paddle_server', 'qingtong', 'danzhengtong'], unavailable = [], failSave = false, failRead = false, optionalNodes = true} = {}) {
  const targets = Object.fromEntries(stages.map(stage => [stage, node({target:stage})]));
  const cards = Object.fromEntries(stages.map(stage => [stage, node()]));
  const statuses = Object.fromEntries(stages.map(stage => [stage, node()]));
  const methods = stages.flatMap(stage => ['vision', 'paddle', 'paddle_server', stage === 'seal' ? 'qingtong' : 'danzhengtong'].map(method => node({stage, method, available:String(providers.includes(method) && !unavailable.includes(`${stage}:${method}`))})));
  const acceptance = ['seal', 'date', 'signature'].flatMap(stage => ['any', 'all', 'none'].map(mode => {
    const input = node({acceptanceInput:stage}); input.value = mode; input.checked = mode === 'any'; return input;
  }));
  const rejection = ['any_mismatch', 'all_mismatch', 'none'].map(mode => {
    const input = node({acceptanceReject:''}); input.value = mode; input.checked = mode === 'none'; return input;
  });
  const presets = ['full', 'date', 'seal', 'seal-test'].map(preset => node({preset}));
  const reset = node({planReset:''});
  const ids = Object.fromEntries(['plan-name', 'plan-summary', 'import-config', 'remote-plan-note', ...(optionalNodes ? ['plan-save-status', 'plan-selected-count', 'plan-selection-list', 'plan-error', 'plan-multi-note', 'plan-requirement-note', 'plan-page-note'] : [])].map(id => [id, node()]));
  const $ = selector => {
    if (selector.startsWith('#')) return ids[selector.slice(1)] || null;
    const match = selector.match(/^\[data-(target|plan-card|plan-status)="([^"]+)"\]$/);
    return match ? ({target:targets, 'plan-card':cards, 'plan-status':statuses}[match[1]][match[2]] || null) : null;
  };
  const $$ = selector => {
    if (selector === '.recognition-plan input') return [...Object.values(targets), ...methods];
    if (selector === '.recognition-plan button, [data-target]') return [...presets, reset, ...Object.values(targets)];
    if (selector === '[data-preset]') return presets;
    if (selector === '[data-plan-reset]') return [reset];
    if (selector === '[data-acceptance-input]') return acceptance;
    if (selector === '[data-acceptance-reject]') return rejection;
    if (selector === '[data-acceptance-input], [data-acceptance-reject]') return [...acceptance, ...rejection];
    const acceptanceMatch = selector.match(/^\[data-acceptance-input="([^"]+)"\]$/);
    if (acceptanceMatch) return acceptance.filter(el => el.dataset.acceptanceInput === acceptanceMatch[1]);
    const match = selector.match(/^\[data-stage="([^"]+)"\]$/);
    return match ? methods.filter(el => el.dataset.stage === match[1]) : [];
  };
  const writes = [], state = {batchRunning:false};
  const controller = createRecognitionPlan({environment:{localStorage:{
    getItem() { if (failRead) throw Error('read unavailable'); return typeof saved === 'string' ? saved : JSON.stringify(saved); },
    setItem(key, value) { if (failSave) throw Error('quota exceeded'); writes.push({key, plan:JSON.parse(value)}); },
  }}, ui:{$, $$}, importsState:state});
  controller.initialize();
  return {controller, targets, cards, statuses, ids, methods, reset, writes, state,
    acceptance, rejection,
    preset:name => presets.find(button => button.dataset.preset === name),
    method:(stage, method) => methods.find(el => el.dataset.stage === stage && el.dataset.method === method),
  };
}

test('acceptance rules support any, all and no comparison for all three checks', () => {
  const h = harness();
  for (const stage of ['seal', 'date', 'signature']) {
    for (const input of h.acceptance.filter(el => el.dataset.acceptanceInput === stage)) input.checked = input.value === 'none';
  }
  h.rejection.forEach(input => { input.checked = input.value === 'all_mismatch'; });
  assert.deepEqual(h.controller.readAcceptancePolicy(), {
    seal_match_mode: 'none', date_match_mode: 'none', signature_match_mode: 'none', reject_mode: 'all_mismatch',
    seal_pass_standard: 'any_exact', date_source: 'danzhengtong',
  });
});

test('seal test selects only Vision fields and QingTong seals, and restores its name from the actual saved plan', () => {
  const h = harness();
  h.preset('seal-test').fire('click');
  assert.deepEqual(h.controller.readRecognitionPlan(), sealTest());
  assert.equal(h.ids['plan-name'].textContent, '印章测试');
  assert.equal(h.preset('seal-test').attributes['aria-pressed'], 'true');
  assert.equal(h.preset('seal-test').classList.contains('active'), true);
  assert.equal(h.ids['plan-selected-count'].textContent, '2 / 5 项');
  assert.equal(h.ids['plan-requirement-note'].classList.contains('hidden'), true);
  assert.equal(h.ids['remote-plan-note'].classList.contains('hidden'), false);
  const restored = harness({saved:h.writes.at(-1).plan});
  assert.deepEqual(restored.controller.readRecognitionPlan(), sealTest());
  assert.equal(restored.ids['plan-name'].textContent, '印章测试');
  assert.equal(restored.writes.length, 0, 'opening settings must not rewrite the saved configuration');
  restored.method('fields', 'paddle').checked = true;
  restored.method('fields', 'paddle').fire('change');
  assert.equal(restored.ids['plan-name'].textContent, '自定义');
  assert.equal(restored.preset('seal-test').attributes['aria-pressed'], 'false');
  restored.method('fields', 'paddle').checked = false;
  restored.method('fields', 'paddle').fire('change');
  assert.equal(restored.ids['plan-name'].textContent, '印章测试');
});

test('seal test availability is checked on the required stage and a disabled preset cannot change the plan', () => {
  for (const [unavailable, reason] of [['fields:vision', 'Vision'], ['seal:qingtong', '清瞳']]) {
    const h = harness({unavailable:[unavailable]});
    const before = h.controller.readRecognitionPlan();
    assert.equal(h.preset('seal-test').disabled, true);
    assert.match(h.preset('seal-test').title, new RegExp(reason));
    h.preset('seal-test').fire('click');
    assert.deepEqual(h.controller.readRecognitionPlan(), before);
    assert.equal(h.writes.length, 0);
  }
});

test('an enabled stage without a method clears the stale import summary and does not save an invalid plan', () => {
  const h = harness({saved:sealTest()});
  const previousSummary = h.ids['import-config'].textContent;
  h.method('fields', 'vision').checked = false;
  h.method('fields', 'vision').fire('change');
  assert.throws(() => h.controller.readRecognitionPlan(), /印刷字段/);
  assert.notEqual(h.ids['import-config'].textContent, previousSummary);
  assert.match(h.ids['import-config'].textContent, /配置未完成/);
  assert.equal(h.cards.fields.classList.contains('invalid'), true);
  assert.equal(h.targets.fields.attributes['aria-invalid'], 'true');
  assert.equal(h.statuses.fields.textContent, '请选择方式');
  assert.equal(h.ids['plan-error'].classList.contains('hidden'), false);
  assert.equal(h.ids['plan-save-status'].attributes['data-state'], 'invalid');
  assert.equal(h.preset('seal-test').attributes['aria-pressed'], 'false');
  assert.equal(h.writes.length, 0);
  h.targets.fields.checked = false;
  h.targets.fields.fire('change');
  assert.deepEqual(h.controller.readRecognitionPlan(), {...emptyPlan(), seal:['qingtong']});
  assert.equal(h.cards.fields.classList.contains('invalid'), false);
  assert.equal(h.statuses.fields.textContent, '未启用');
  assert.equal(h.ids['plan-requirement-note'].classList.contains('hidden'), false);
  assert.equal(h.ids['plan-page-note'].classList.contains('hidden'), true);
});

test('unavailable restored methods require a new selection without replacing the saved browser plan', () => {
  const h = harness({saved:sealTest(), unavailable:['fields:vision']});
  assert.equal(h.targets.fields.checked, true);
  assert.equal(h.method('fields', 'vision').checked, false);
  assert.equal(h.method('fields', 'vision').disabled, true);
  assert.throws(() => h.controller.readRecognitionPlan(), /印刷字段/);
  assert.equal(h.cards.fields.classList.contains('invalid'), true);
  assert.equal(h.writes.length, 0);
});

test('reset preserves normal defaults, falls back to Server, and never silently selects a simulated provider', () => {
  const normal = harness({saved:sealTest()});
  normal.reset.fire('click');
  assert.deepEqual(normal.controller.readRecognitionPlan(), {fields:['paddle'], products:['paddle'], handwriting:[], date:['vision'], seal:['vision']});
  assert.equal(normal.ids['plan-name'].textContent, '默认方案');
  const server = harness({providers:['paddle_server', 'danzhengtong']});
  assert.deepEqual(server.controller.readRecognitionPlan(), {fields:['paddle_server'], products:['paddle_server'], handwriting:[], date:['paddle_server'], seal:['paddle_server']});
  server.preset('full').fire('click');
  assert.deepEqual(server.controller.readRecognitionPlan().handwriting, ['paddle_server']);
  const none = harness({providers:['danzhengtong']});
  none.reset.fire('click');
  assert.throws(() => none.controller.readRecognitionPlan(), /至少选择/);
  assert.equal(none.preset('full').disabled, true);
  assert.equal(none.preset('seal-test').disabled, true);
  assert.equal(none.methods.some(el => el.checked), false);
  assert.equal(none.writes.length, 0);
});

test('storage failure is reported without blocking a valid current selection', () => {
  const h = harness({failSave:true});
  h.preset('seal-test').fire('click');
  assert.deepEqual(h.controller.readRecognitionPlan(), sealTest());
  assert.match(h.ids['plan-save-status'].textContent, /未能保存/);
  assert.equal(h.ids['plan-save-status'].attributes['data-state'], 'error');
  assert.match(h.ids['import-config'].textContent, /Vision/);
  const unreadable = harness({failRead:true});
  assert.deepEqual(unreadable.controller.readRecognitionPlan(), unreadable.controller.defaultPlan());
  assert.match(unreadable.ids['plan-save-status'].textContent, /未能读取/);
  assert.equal(unreadable.ids['plan-save-status'].attributes['data-state'], 'error');
  assert.equal(unreadable.writes.length, 0);
});

test('malformed saved values and unknown providers never become executable or rendered markup', () => {
  const h = harness({saved:{fields:['vision', '<img src=x onerror=alert(1)>', 4, {bad:true}], products:'vision', seal:['qingtong'], unknown:['vision']}});
  assert.deepEqual(h.controller.readRecognitionPlan(), sealTest());
  assert.doesNotMatch(h.ids['plan-selection-list'].innerHTML, /<img|onerror|undefined/);
  assert.equal(h.writes.length, 0);
  const outerArray = harness({saved:'["vision"]'});
  assert.deepEqual(outerArray.controller.readRecognitionPlan(), outerArray.controller.defaultPlan());
  const oldMarkup = harness({optionalNodes:false});
  assert.doesNotThrow(() => oldMarkup.preset('seal-test').fire('click'));
});

test('multiple providers, disabled stages, and upload locking keep their status consistent', () => {
  const h = harness({saved:{...emptyPlan(), fields:['vision', 'paddle'], seal:['qingtong']}});
  assert.equal(h.ids['plan-multi-note'].classList.contains('hidden'), false);
  assert.equal(h.method('date', 'vision').disabled, true);
  h.targets.fields.checked = false;
  h.targets.fields.fire('change');
  assert.equal(h.ids['plan-multi-note'].classList.contains('hidden'), true);
  assert.deepEqual(h.controller.readRecognitionPlan().fields, []);
  assert.equal(h.method('fields', 'vision').checked, true, 'a temporarily disabled stage retains its choices');
  assert.equal(h.method('fields', 'vision').disabled, true);
  h.targets.fields.checked = true;
  h.targets.fields.fire('change');
  assert.equal(h.ids['plan-multi-note'].classList.contains('hidden'), false);
  const before = h.controller.readRecognitionPlan();
  h.state.batchRunning = true;
  h.controller.updateRecognitionPlan();
  assert.equal(h.reset.disabled, true);
  assert.equal(h.preset('seal-test').disabled, true);
  assert.equal(h.methods.every(el => el.disabled), true);
  assert.equal(Object.values(h.targets).every(el => el.disabled), true);
  h.preset('seal-test').fire('click'); h.reset.fire('click');
  assert.deepEqual(h.controller.readRecognitionPlan(), before);
  h.state.batchRunning = false;
  h.controller.updateRecognitionPlan();
  assert.equal(h.preset('seal-test').disabled, false);
  assert.equal(h.method('date', 'vision').disabled, true);
  assert.equal(h.method('fields', 'vision').disabled, false);
});
