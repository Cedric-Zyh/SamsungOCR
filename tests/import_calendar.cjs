const assert = require('node:assert/strict');
const test = require('node:test');
const {createImportCalendar} = require('../static/modules/import_calendar.mjs');

function node() {
  const listeners = new Map(), classes = new Set();
  return {value: '', textContent: '', innerHTML: '', attributes: {},
    classList: {add: key => classes.add(key), remove: key => classes.delete(key), contains: key => classes.has(key)},
    setAttribute(key, value) { this.attributes[key] = value; }, focus() { this.focused = true; },
    addEventListener(name, listener) { listeners.set(name, listener); },
    emit(name, event = {}) { return listeners.get(name)?.({target: this, ...event}); },
  };
}
function harness(prefix = 'calendar') {
  const nodes = new Map(), calls = [], selected = [];
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); };
  const h = {day: '2026-09-10', respond: async () => ({'2026-09-10': 120})};
  const environment = {document: node(), window: node()};
  const panel = $('#popup'); panel.classList.add('hidden');
  const calendar = createImportCalendar({environment, ui: {$}, prefix, popup: '#popup',
    readDate: () => h.day, selectDate: day => { h.day = day; selected.push(day); },
    api: url => { calls.push(url); return h.respond(url); }});
  calendar.initialize();
  return Object.assign(h, {part: name => $(`#${prefix}-${name}`), panel, environment, calendar, calls, selected});
}

for (const prefix of ['calendar', 'progress-calendar']) {
  test(`${prefix} renders receipt counts and selects dates without changing the other picker`, async () => {
    const h = harness(prefix), other = harness(prefix === 'calendar' ? 'progress-calendar' : 'calendar');
    await h.part('toggle').emit('click');
    assert.match(h.part('days').innerHTML, /aria-label="2026-09-10，120 张回单" aria-pressed="true"/);
    assert.match(h.part('days').innerHTML, /title="120 张回单">99\+</);
    await h.part('days').emit('click', {target: {closest: () => ({dataset: {calendarDate: '2026-09-09'}})}});
    assert.deepEqual(h.selected, ['2026-09-09']);
    assert.equal(h.part('toggle').textContent, '2026/09/09');
    assert.equal(h.part('toggle').attributes['aria-expanded'], 'false');
    assert.equal(h.part('toggle').focused, true);
    assert.equal(other.day, '2026-09-10');
    assert.equal(other.calls.length, 0);
  });
}

test('month navigation handles year boundaries, leap days, and invalid month input', async () => {
  const h = harness(); h.day = '2027-01-01';
  await h.part('toggle').emit('click'); await h.part('prev').emit('click');
  assert.equal(h.part('month').value, '2026-12');
  h.part('month').value = '2028-02'; await h.part('month').emit('change');
  assert.match(h.part('days').innerHTML, /data-calendar-date="2028-02-29"/);
  assert.doesNotMatch(h.part('days').innerHTML, /2028-02-30/);
  const count = h.calls.length;
  h.part('month').value = '2028-13'; await h.part('month').emit('change');
  assert.equal(h.part('month').value, '2028-02'); assert.equal(h.calls.length, count);
});

test('late counts cannot replace a newer month or a closed calendar', async () => {
  const h = harness(), pending = [];
  h.respond = () => new Promise(resolve => pending.push(resolve));
  const first = h.part('toggle').emit('click'), next = h.part('next').emit('click');
  pending[1]({}); await next; const october = h.part('days').innerHTML;
  pending[0]({'2026-09-10': 120}); await first;
  assert.equal(h.part('days').innerHTML, october); assert.match(october, /2026-10-31/);
  const earlier = h.part('prev').emit('click');
  h.environment.document.emit('keydown', {key: 'Escape'});
  pending[2]({}); await earlier;
  assert.equal(h.panel.classList.contains('hidden'), true);
  assert.equal(h.part('days').innerHTML, '');
});

test('failed receipt counts leave dates selectable and navigation dismisses the popup', async () => {
  const h = harness(); h.respond = async () => { throw Error('offline'); };
  await h.part('toggle').emit('click');
  assert.match(h.part('note').textContent, /数量加载失败/);
  assert.match(h.part('days').innerHTML, /2026-09-10，数量未知/);
  h.environment.window.emit('hashchange');
  assert.equal(h.panel.classList.contains('hidden'), true);
  assert.equal(h.part('toggle').attributes['aria-expanded'], 'false');
});
