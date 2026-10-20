const assert = require('node:assert/strict');
const test = require('node:test');
const {createReviewStandard} = require('../static/modules/seals/review_standard.mjs');
const {candidateCard, standardCard} = require('../static/modules/seals/standard_view.mjs');

test('information stamp card explains field matching and escapes the explanation', () => {
  const markup = candidateCard({id:'info', index:0, can_register:true,
    image_url:'/stamp.png', text_status:'匹配', shape_status:'尚无参照',
    text_match_message:'名称、授权代码一致；地址、电话为附加信息 <script>bad</script>'});
  assert.match(markup, /名称、授权代码一致；地址、电话为附加信息/);
  assert.ok(markup.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(!markup.includes('<script>'));
});
const {reviewReadiness} = require('../static/modules/review/review_readiness.mjs');

function element() {
  const events = new Map(), classes = new Set();
  return {
    disabled: false, value: '0', style: {}, dataset: {}, textContent: '',
    classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
    setAttribute() {}, checkValidity: () => true, reportValidity() {},
    addEventListener(name, callback) { events.set(name, callback); },
    fire(name) { return events.get(name)?.({stopPropagation() {}}); },
  };
}
function harness({mode = 'review', standard = null, sampleView = false} = {}) {
  const nodes = new Map();
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const candidates = [{id:'first',index:0,can_register:true}, {id:'raw',index:1,can_register:false}];
  const buttons = candidates.map(c => Object.assign(element(), {dataset:{sealCandidate:c.id},disabled:!c.can_register}));
  const unrelated = element(), controls = [...buttons, unrelated, $('[data-standard-save]'), $('[data-standard-rotation]')];
  const $$ = selector => selector === '[data-seal-candidate]' ? buttons : controls;
  const item = {id:7,review_revision:'receipt-1',seal_standard_check:{standard,candidates}};
  const reviewState = {current:item,reviewSaving:false,reviewDirty:false,sampleView};
  let resolveSave, rejectSave, calls = [], routes = [], saved;
  const save = route => (...args) => {
    routes.push(route);
    calls.push(args); return new Promise((resolve,reject) => {resolveSave=resolve;rejectSave=reject;});
  };
  const services = {sealStandards:{save:save('receipt')},samples:{saveStandard:save('sample')}};
  createReviewStandard({ui:{$,$$,toast(){}},reviewState,services,onSaved:value=>saved=value})(item,{},mode);
  return {$,buttons,controls,unrelated,reviewState,calls,routes,resolve:value=>resolveSave(value),reject:error=>rejectSave(error),saved:()=>saved};
}

test('viewing and selecting a candidate do not register; save locks the entire review and sends its revision', async () => {
  const h = harness();
  assert.equal(h.calls.length,0);
  h.buttons[0].fire('click');
  assert.equal(h.calls.length,0);
  h.$('[data-standard-rotation]').value='90';
  const pending=h.$('[data-standard-save]').fire('click');
  assert.equal(h.reviewState.reviewSaving,true);
  assert(h.controls.every(el=>el.disabled));
  h.$('[data-standard-save]').fire('click');
  assert.equal(h.calls.length,1);
  assert.deepEqual(h.calls[0],[7,{candidate_id:'first',standard_revision:0,review_revision:'receipt-1',rotation:90}]);
  h.resolve({result:{id:7,seal_standard_check:{status:'matched'}}}); await pending;
  assert.equal(h.saved().seal_standard_check.status,'matched');
  assert.equal(h.reviewState.reviewSaving,false);
  assert.equal(h.unrelated.disabled,false);
  assert.equal(h.buttons[1].disabled,true);
});

test('unsaved receipt edits block standard writes and preserve the selected stamp', async () => {
  const h=harness();h.buttons[0].fire('click');h.reviewState.reviewDirty=true;
  await h.$('[data-standard-save]').fire('click');
  assert.equal(h.calls.length,0);
  assert.match(h.$('[data-standard-feedback]').textContent,/未保存/);
  assert.equal(h.reviewState.reviewDirty,true);
});

test('replacement explicitly submits the current standard revision and restores controls on conflicts', async () => {
  const h=harness({standard:{id:1,image_url:'/stamp.png',source_filename:'A',revision:4}});
  h.buttons[0].fire('click');const pending=h.$('[data-standard-save]').fire('click');
  assert.equal(h.calls[0][1].standard_revision,4);
  h.reject(new Error('标准章已变化'));await pending;
  assert.equal(h.saved(),undefined);
  assert.match(h.$('[data-standard-feedback]').textContent,/标准章已变化/);
  assert.equal(h.unrelated.disabled,false);assert.equal(h.buttons[1].disabled,true);
});

test('view mode allows explicit standard registration and preserves read-only receipt fields', async () => {
  for (const sampleView of [false, true]) {
    const h=harness({mode:'view',sampleView});
    h.unrelated.disabled=true;
    assert.equal(h.buttons[0].disabled,false);
    assert.equal(h.buttons[1].disabled,true);
    h.buttons[0].fire('click');
    assert.equal(h.calls.length,0);
    const pending=h.$('[data-standard-save]').fire('click');
    assert.deepEqual(h.routes,[sampleView ? 'sample' : 'receipt']);
    h.resolve({result:{id:7,sample_center:sampleView}});await pending;
    assert.equal(h.saved().sample_center,sampleView);
    assert.equal(h.unrelated.disabled,true);
    assert.equal(h.buttons[1].disabled,true);
  }
});

test('text match alone is not ready without a shape reference; explicit manual confirmation is independent', () => {
  const item={fields:{'要求到货':'2026-10-12','签章要求':'客户章'},date_check:{actual:'2026-10-12',status:'匹配',reliable:true},
    seal_check:{recognized:'客户章',status:'匹配',reliable:true},seal_standard_check:{requirement:'客户章',status:'missing'}};
  const draft={requiredDate:'2026-10-12',actualDate:'2026-10-12',sealText:'客户章',sealRequirement:'客户章',sealConfirmed:null};
  assert.equal(reviewReadiness(item,draft).ready,false);
  assert.match(reviewReadiness(item,draft).message,/尚无标准章/);
  assert.equal(reviewReadiness(item,{...draft,sealConfirmed:true}).ready,true);
  item.seal_standard_check.status='matched';assert.equal(reviewReadiness(item,draft).ready,true);
});

test('OCR text and source filename are escaped, while unknown shapes remain unavailable for registration', () => {
  const markup=candidateCard({id:'one',recognized:'<img src=x onerror=1>',can_register:false,original_url:'/stamp.png',index:0},{filename:'<script>bad</script>'});
  assert.doesNotMatch(markup,/<script>|<img src=x/);assert.match(markup,/&lt;img/);assert.match(markup,/disabled/);
  assert.match(standardCard(null),/不会自动加入/);
});
