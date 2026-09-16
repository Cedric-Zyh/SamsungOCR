import {localToday} from './ui.mjs';

// Both import-date pickers use the same month navigation and receipt counts.
export function createImportCalendar({environment, ui, api, prefix, popup, readDate, selectDate}) {
  const {$} = ui;
  const part = name => $(`#${prefix}-${name}`);
  const panel = () => $(popup);
  const validMonth = value => /^\d{4}-(0[1-9]|1[0-2])$/.test(value) && !value.startsWith('0000');
  let month = localToday().slice(0, 7), request = 0;

  function sync() {
    const toggle = part('toggle');
    if (toggle) toggle.textContent = (readDate() || localToday()).replaceAll('-', '/');
  }
  function close() {
    request++;
    panel().classList.add('hidden');
    part('toggle').setAttribute('aria-expanded', 'false');
  }
  async function render() {
    const version = ++request, renderedMonth = month;
    part('month').value = month;
    part('days').innerHTML = '';
    part('note').textContent = '正在加载导入记录…';
    const first = new Date(`${month}-01T12:00:00`);
    const end = new Date(first); end.setMonth(end.getMonth() + 1, 0);
    let counts = {}, failed = false;
    try { counts = await api(`/api/import-dates?month=${month}`); }
    catch (_) { failed = true; }
    if (version !== request) return;
    part('note').textContent = failed ? '数量加载失败，请重新打开日历重试' : '右上角数字表示当天导入的回单数量';
    const cells = Array.from({length: first.getDay()}, () => '<span></span>');
    for (let day = 1; day <= end.getDate(); day++) {
      const date = `${renderedMonth}-${String(day).padStart(2, '0')}`;
      const count = Math.max(0, Math.trunc(Number(counts?.[date]) || 0));
      const selected = date === readDate();
      cells.push(`<button type="button" data-calendar-date="${date}" class="calendar-day ${selected ? 'selected' : ''} ${date === localToday() ? 'is-today' : ''}" aria-label="${date}，${failed ? '数量未知' : `${count} 张回单`}" aria-pressed="${selected}"><span>${day}</span>${count ? `<sup title="${count} 张回单">${count > 99 ? '99+' : count}</sup>` : ''}</button>`);
    }
    part('days').innerHTML = cells.join('');
  }
  function shift(offset) {
    const date = new Date(`${month}-01T12:00:00`);
    date.setMonth(date.getMonth() + offset);
    const next = `${String(date.getFullYear()).padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}`;
    if (!validMonth(next)) return;
    month = next;
    return render();
  }
  function initialize() {
    if (!part('toggle')) return;
    sync();
    part('toggle').addEventListener('click', () => {
      if (!panel().classList.contains('hidden')) return close();
      const selectedMonth = (readDate() || '').slice(0, 7);
      month = validMonth(selectedMonth) ? selectedMonth : localToday().slice(0, 7);
      panel().classList.remove('hidden');
      part('toggle').setAttribute('aria-expanded', 'true');
      const pending = render();
      part('month').focus();
      return pending;
    });
    part('prev').addEventListener('click', () => shift(-1));
    part('next').addEventListener('click', () => shift(1));
    part('month').addEventListener('change', event => {
      if (!validMonth(event.target.value)) { event.target.value = month; return; }
      month = event.target.value;
      return render();
    });
    part('days').addEventListener('click', event => {
      const button = event.target.closest('[data-calendar-date]');
      if (!button) return;
      close();
      selectDate(button.dataset.calendarDate);
      sync();
      part('toggle').focus();
    });
    environment.document.addEventListener('click', event => {
      if (!event.target.closest('.calendar-wrap')) close();
    });
    environment.document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !panel().classList.contains('hidden')) {
        close(); part('toggle').focus();
      }
    });
    environment.window.addEventListener?.('hashchange', close);
  }
  return {initialize, sync, close};
}
