const assert = require('node:assert/strict');
const test = require('node:test');
const {reviewReadiness, reviewKeyboardAction, normalizedReviewDate} = require('../static/modules/review_readiness.mjs');
const {createReviewImage} = require('../static/modules/review_image.mjs');
const {createReview} = require('../static/modules/review.mjs');
const {dateAuditText, sealEvidenceMarkup} = require('../static/modules/review_provider_evidence.mjs');
const workbench = require('../static/workbench.js');

const item = () => ({id: 1, fields: {'要求到货': '2025-03-31', '签章要求': '上海客户收货章'},
  date_check: {actual: '2025-03-31', status: '匹配', reliable: true},
  seal_check: {recognized: '上海客户收货章', status: '匹配', reliable: true}});
const draft = () => ({requiredDate: '2025-03-31', actualDate: '2025-03-31', dateConfirmed: false,
  sealText: '上海客户收货章', sealRequirement: '上海客户收货章', sealConfirmed: null});

test('reliable machine matches remain ready without requiring repeat human confirmation', () => {
  assert.equal(reviewReadiness(item(), draft()).ready, true);
  const untrusted = item(); untrusted.date_check.reliable = false; untrusted.seal_check.reliable = false;
  assert.equal(reviewReadiness(untrusted, draft()).ready, false);
  assert.equal(reviewReadiness(untrusted, {...draft(), dateConfirmed: true, sealConfirmed: true}).ready, true);
});

test('date evidence keeps provider provenance together with accepted and rejected date evidence', () => {
  const evidence = dateAuditText({date_check: {backend: '单证通', actual: '2025-02-10',
    rejected_candidates: [{value: '2025-02-01', reason: '日期冲突'}],
    business_time_overridden_candidates: [{value: '2025-02-09', reason: '跨模型一致'}]}});
  assert.match(evidence, /来源：单证通 · 2025-02-10/);
  assert.match(evidence, /已拒绝 2025-02-01：日期冲突/);
  assert.match(evidence, /已由跨模型共识覆盖 2025-02-09：跨模型一致/);
});

test('date evidence includes an additional Danzhengtong result and avoids duplicating the selected provider', () => {
  const provider = {actual: '2025-02-10', source: '单证通', status: '匹配'};
  const record = {date_check: {actual: '2025-02-09', source: 'Paddle', status: '不匹配'},
    recognition_variants: {date: [{method: 'danzhengtong', details: {date_check: provider}}]}};
  assert.match(dateAuditText(record), /单证通：2025-02-10 · 匹配/);
  record.date_check = provider;
  assert.equal(dateAuditText(record), '来源：单证通 · 2025-02-10');
  record.recognition_variants.date[0].error = '接口失败';
  assert.match(dateAuditText(record), /单证通：2025-02-10 · 识别失败/);
});

test('seal evidence shows QingTong and Danzhengtong comparisons independently of the overall result', () => {
  const record = {seal_check: {status: '部分匹配', dual_check: {selected: {
    template: {matched: false, label: '其他公司', similarity: .84},
    ocr: {matched: false, text: '太原市伊服壹电子服务总汇'}
  }}}, recognition_variants: {seal: [{method: 'danzhengtong', details: {
    seal_check: {recognized: '太原市伊加壹电子服务总汇', status: '匹配', reliable: true}
  }}]}};
  const markup = sealEvidenceMarkup(record);
  assert.match(markup, /清瞳 · 印章模板识别<\/span><span class="pill danger">不匹配/);
  assert.match(markup, /清瞳 · 印章文字 OCR<\/span><span class="pill danger">不匹配/);
  assert.match(markup, /单证通 · 印章文字 OCR<\/span><span class="pill success">匹配/);
  assert.match(markup, /太原市伊服壹电子服务总汇/);
  assert.match(markup, /太原市伊加壹电子服务总汇/);
});

test('single-provider seal evidence works without variants and preserves simulated provenance', () => {
  assert.match(sealEvidenceMarkup({seal_check: {backend: '单证通', recognized: '客户公司', status: '匹配'}}),
    /单证通 · 印章文字 OCR/);
  assert.match(sealEvidenceMarkup({seal_check: {recognition_mode: 'danzhengtong', simulated: true,
    recognized: '客户公司', status: '匹配'}}), /单证通（模拟） · 印章文字 OCR/);
  assert.equal(sealEvidenceMarkup({seal_check: {backend: '本地', recognized: '客户公司'}}), '');
});

test('QingTong evidence remains visible when the selected result comes from Danzhengtong', () => {
  const markup = sealEvidenceMarkup({seal_check: {backend: '单证通', recognized: '客户公司', status: '匹配'},
    recognition_variants: {seal: [{method: 'qingtong', details: {seal_check: {dual_check: {selected: {
      template: {matched: true, label: '客户公司'}, ocr: {matched: true, text: '客户公司'}
    }}}}}]}});
  assert.match(markup, /清瞳 · 印章模板识别/);
  assert.match(markup, /清瞳 · 印章文字 OCR/);
  assert.match(markup, /单证通 · 印章文字 OCR/);
});

test('seal evidence preserves exact, partial and mismatched channel statuses', () => {
  const markup = sealEvidenceMarkup({seal_check: {status: '匹配', message: '任一识别结果完全符合签章要求',
    dual_check: {selected: {
      template: {matched: false, label: '上海客户收货', requirement_match: {status: '部分匹配'}},
      ocr: {matched: false, text: '上海客户错货章', comparison: {status: '不匹配'}}
    }}}, recognition_variants: {seal: [{method: 'danzhengtong', details: {
      seal_check: {recognized: '上海客户收货章', status: '匹配'}
    }}]}});
  assert.match(markup, /清瞳 · 印章模板识别<\/span><span class="pill warning">部分匹配/);
  assert.match(markup, /清瞳 · 印章文字 OCR<\/span><span class="pill danger">不匹配/);
  assert.match(markup, /单证通 · 印章文字 OCR<\/span><span class="pill success">匹配/);
  assert.match(markup, /模板名称独立对照本单签章要求/);
});

test('explicit channel statuses work without detailed comparisons and the summary uses the winning aggregate', () => {
  const markup = sealEvidenceMarkup({seal_check: {backend: '单证通', recognized: '上海客户收货章', status: '匹配',
    message: '单证通印章文字符合签章要求'}, recognition_variants: {seal: [{method: 'qingtong', details: {
      seal_check: {message: '旧清瞳结论：需人工复核', dual_check: {selected: {
        template: {status: '不匹配', label: '其他客户'},
        ocr: {status: '部分匹配', text: '上海客户收货'}
      }}}
    }}]}});
  assert.match(markup, /清瞳 · 印章模板识别<\/span><span class="pill danger">不匹配/);
  assert.match(markup, /清瞳 · 印章文字 OCR<\/span><span class="pill warning">部分匹配/);
  assert.match(markup, /单证通印章文字符合签章要求/);
  assert.doesNotMatch(markup, /旧清瞳结论/);
});

test('empty and failed provider evidence never shows a match, and provider text is escaped', () => {
  const empty = sealEvidenceMarkup({seal_check: {backend: '单证通', recognized: '', status: '匹配'}});
  assert.match(empty, />未识别<\/span>/);
  assert.doesNotMatch(empty, /pill success/);
  const failed = sealEvidenceMarkup({recognition_variants: {seal: [{method: 'danzhengtong', error: 'failed',
    details: {seal_check: {recognized: '<img src=x onerror="alert(1)">', status: '匹配'}}}]}});
  assert.match(failed, />识别失败<\/span>/);
  assert.doesNotMatch(failed, /pill success|<img/);
  assert.match(failed, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});

test('date mismatch and explicit negative stamp decisions prevent confirmation', () => {
  const result = reviewReadiness(item(), {...draft(), actualDate: '2025-04-01', dateConfirmed: true, sealConfirmed: false});
  assert.equal(result.ready, false);
  assert.equal(result.dateStatus, '不匹配'); assert.equal(result.sealStatus, '不匹配');
  assert.equal(result.issues.length, 2);
  assert.match(result.message, /日期与要求不一致/);
});

test('unchanged partial seal recognition stays partial until edited or explicitly confirmed', () => {
  const record = item();
  record.seal_check = {recognized: '上海客户收货', status: '部分匹配', reliable: false};
  const values = {...draft(), sealText: '上海客户收货'};
  const result = reviewReadiness(record, values);
  assert.equal(result.sealStatus, '部分匹配');
  assert.equal(result.ready, false);
  assert.match(result.message, /印章文字部分匹配/);
  assert.equal(reviewReadiness(record, {...values, sealConfirmed: true}).sealStatus, '匹配');
  assert.equal(reviewReadiness(record, {...values, sealConfirmed: false}).sealStatus, '不匹配');
  assert.equal(reviewReadiness(record, {...values, sealText: '上海客户收货章'}).sealStatus, '待确认');
});

test('changed seal text or requirement cannot reuse the old machine match', () => {
  assert.equal(reviewReadiness(item(), {...draft(), sealText: '另一个客户'}).ready, false);
  assert.equal(reviewReadiness(item(), {...draft(), sealRequirement: '另一个客户'}).ready, false);
  assert.equal(reviewReadiness(item(), {...draft(), sealText: '', sealConfirmed: true}).ready, false);
});

test('date readiness accepts printed formats but rejects impossible calendar dates', () => {
  assert.equal(normalizedReviewDate('2025年3月31日'), '2025-03-31');
  assert.equal(normalizedReviewDate('2025-02-29'), '');
  assert.equal(reviewReadiness(item(), {...draft(), requiredDate: '2025年3月31日', dateConfirmed: true}).ready, true);
});

test('review keyboard shortcuts do not steal ordinary input, composition or unrelated modifier combinations', () => {
  const input = {closest: () => ({})}, body = {closest: () => null};
  const key = (value, overrides = {}) => reviewKeyboardAction({key: value, target: body, ...overrides});
  assert.equal(key('s', {ctrlKey: true, target: input}), 'save');
  assert.equal(key('Enter', {metaKey: true, target: input}), 'pass');
  assert.equal(key('d', {altKey: true}), 'date');
  assert.equal(key('s', {altKey: true}), 'seal');
  assert.equal(key('+'), 'zoom-in');
  for (const value of ['s', 'd', '+', '-']) assert.equal(key(value, {target: input}), '');
  assert.equal(key('d', {target: input, altKey: true}), '');
  assert.equal(key('Enter', {ctrlKey: true, isComposing: true}), '');
  assert.equal(key('s', {metaKey: true, repeat: true}), '');
  assert.equal(key('s', {ctrlKey: true, shiftKey: true}), '');
});

function element() {
  const events = new Map(), classes = new Set(), captures = new Set();
  return {value: '', textContent: '', innerHTML: '', disabled: false, checked: false, dataset: {}, attributes: {},
    style: {setProperty(key, value) { this[key] = value; }},
    classList: {contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name),
      toggle: (name, value) => value ? classes.add(name) : classes.delete(name)},
    addEventListener(type, listener) { if (!events.has(type)) events.set(type, []); events.get(type).push(listener); },
    async dispatch(type, overrides = {}) { const event = {target: this, key: '', button: 0, pointerId: 1, preventDefault() { this.prevented = true; }, ...overrides};
      for (const listener of events.get(type) || []) await listener(event); return event; },
    getBoundingClientRect: () => ({left: 0, top: 0, width: 1000, height: 400}),
    setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id), releasePointerCapture: id => captures.delete(id),
    setAttribute(name, value) { this.attributes[name] = value; },
    scrollLeft: 100, scrollTop: 200, clientWidth: 500, clientHeight: 400,
    scrollTo(left, top) { this.scrollLeft = left; this.scrollTop = top; }, focus() {}, closest: () => null};
}

function imageHarness(storage = new Map()) {
  const nodes = new Map(), $ = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const divider = $('[data-review-divider]'); divider.parentElement = element();
  const reviewState = {reviewZoom: 1};
  const environment = {localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)}};
  const controller = createReviewImage({environment, ui: {$, $$: () => []}, reviewState});
  controller.attach({});
  return {controller, $, reviewState, storage};
}

test('modified wheel zoom keeps the pointer over the same image coordinate; ordinary wheel remains scrolling', async () => {
  const h = imageHarness(), viewport = h.$('[data-image-viewport]'), image = h.$('[data-preview]');
  image.getBoundingClientRect = () => ({left: -100, top: -200});
  const normal = await viewport.dispatch('wheel', {deltaY: 50, clientX: 200, clientY: 100});
  assert.equal(normal.prevented, undefined); assert.equal(h.reviewState.reviewZoom, 1);
  const zoomed = await viewport.dispatch('wheel', {ctrlKey: true, deltaY: -Math.log(2) / .002, clientX: 200, clientY: 100});
  assert.equal(zoomed.prevented, true); assert.equal(h.reviewState.reviewZoom, 2);
  assert.equal(viewport.scrollLeft, 400); assert.equal(viewport.scrollTop, 500);
  h.controller.zoom('fit');
  assert.equal(viewport.scrollLeft, 0); assert.equal(viewport.scrollTop, 0);
  assert.equal(image.style.width, '100%');
});

test('pointer panning ends on cancellation and does not interfere with native touch scrolling', async () => {
  const h = imageHarness(), viewport = h.$('[data-image-viewport]');
  await viewport.dispatch('pointerdown', {clientX: 200, clientY: 200});
  await viewport.dispatch('pointermove', {clientX: 150, clientY: 125});
  assert.equal(viewport.scrollLeft, 150); assert.equal(viewport.scrollTop, 275);
  await viewport.dispatch('pointercancel');
  await viewport.dispatch('pointermove', {clientX: 100, clientY: 50});
  assert.equal(viewport.scrollLeft, 150); assert.equal(viewport.classList.contains('is-dragging'), false);
  const touch = await viewport.dispatch('pointerdown', {pointerType: 'touch', clientX: 150, clientY: 125});
  assert.equal(touch.prevented, undefined);
});

test('divider pointer and keyboard changes persist while enforcing usable panel widths', async () => {
  const h = imageHarness(), divider = h.$('[data-review-divider]');
  await divider.dispatch('pointerdown');
  await divider.dispatch('pointermove', {clientX: 950});
  await divider.dispatch('pointerup');
  assert.equal(divider.attributes['aria-valuenow'], '64');
  const reopened = imageHarness(h.storage), next = reopened.$('[data-review-divider]');
  assert.equal(next.parentElement.style['--review-preview-width'], '64%');
  await next.dispatch('keydown', {key: 'Home'});
  assert.equal(next.attributes['aria-valuenow'], '48');
  await next.dispatch('keydown', {key: 'ArrowLeft'});
  assert.equal(next.attributes['aria-valuenow'], '46');
});

function reviewHarness() {
  const nodes = new Map();
  const $ = selector => { const key = selector.replaceAll('"', ''); if (!nodes.has(key)) nodes.set(key, element()); return nodes.get(key); };
  const reviewState = {current: item(), reviewDeferred: new Set(), reviewEditVersion: 0};
  const form = $('#review-form');
  for (const name of ['actual_date', 'actual_date_confirmed', 'seal_text', 'seal_confirmed_match', 'human_note', 'error_type', 'save_ground_truth', 'truth_seal_should_match']) form[name] = $(`[name=${name}]`);
  $('[name=field:要求到货]').value = '2025-03-31'; $('[name=field:签章要求]').value = '上海客户收货章';
  $('[name=actual_date]').value = '2025-03-31'; $('[name=seal_text]').value = '上海客户收货章';
  const sealChoices = ['same', 'different'].map(choice => { const button = $(`[data-seal-choice=${choice}]`); button.dataset.sealChoice = choice; return button; });
  const controls = [form.actual_date, form.seal_text, $('[data-confirm-pass]'), $('[data-save-pending]')];
  const $$ = selector => selector === '[data-seal-choice]' ? sealChoices : selector === 'button,input,textarea,select' ? controls : [];
  const calls = [], notices = [], deferred = {};
  const controller = createReview({environment: {document: element(), window: {confirm: () => true}},
    ui: {$, $$, toast: message => notices.push(message)}, reviewState, ReceiptWorkbench: workbench,
    api: async (_url, options) => { calls.push(options); return new Promise(resolve => deferred.resolve = resolve); },
    markReviewSaved() {}, continueReview: async () => {}, refreshVisibleResults: async () => {}});
  return {controller, $, reviewState, controls, calls, notices, deferred};
}

test('live readiness and summary resolve immediately after explicit date and stamp choices', async () => {
  const h = reviewHarness(); h.reviewState.current.date_check.reliable = false; h.reviewState.current.seal_check.reliable = false;
  h.controller.setupDateConfirmation(h.reviewState.current, {});
  h.controller.setupSealConfirmation(h.reviewState.current, {});
  h.controller.syncReviewReadiness();
  assert.equal(h.$('[data-confirm-pass]').disabled, true);
  await h.$('[data-date-choice=same]').dispatch('click');
  await h.$('[data-seal-choice=same]').dispatch('click');
  assert.equal(h.$('[data-confirm-pass]').disabled, false);
  assert.match(h.$('[data-review-readiness]').textContent, /可确认通过/);
  assert.match(h.$('[data-status-summary]').innerHTML, /核验信息已齐全/);
  await h.$('[data-seal-change]').dispatch('click');
  assert.equal(h.$('[data-confirm-pass]').disabled, true);
  assert.match(h.$('[data-status-summary]').innerHTML, /印章尚未确认/);
});

test('main recognized seal shows the backend winner instead of the retained QingTong OCR text', () => {
  const h = reviewHarness(), expected = h.reviewState.current.seal_check.recognized;
  h.reviewState.current.seal_check.dual_check = {selected: {
    template: {matched: true, label: expected},
    ocr: {matched: false, text: '上海客户收货', comparison: {status: '部分匹配'}}
  }};
  h.$('[name=seal_text]').value = '上海客户收货';
  h.controller.setupSealConfirmation(h.reviewState.current, {});
  h.controller.syncReviewReadiness();
  assert.equal(h.$('[name=seal_text]').value, expected);
  assert.equal(h.$('[data-seal-value]').textContent, expected);
  assert.match(h.$('[data-check-badge=seal]').innerHTML, />匹配<\/span>/);
  assert.equal(h.$('[data-confirm-pass]').disabled, false);
});

test('submitting disables all controls and blocks duplicate requests until completion', async () => {
  const h = reviewHarness(); h.controller.syncReviewReadiness();
  const pending = h.controller.submitReview('确认通过', '通过');
  assert.equal(h.reviewState.reviewSaving, true);
  assert(h.controls.every(control => control.disabled));
  await h.controller.submitReview('确认通过', '通过');
  assert.equal(h.calls.length, 1);
  h.deferred.resolve(item()); await pending;
  assert.equal(h.reviewState.reviewSaving, false);
  assert.equal(h.$('[data-confirm-pass]').disabled, false);
});
