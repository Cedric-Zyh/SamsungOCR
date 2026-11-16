import {escapeHtml, localToday} from '../core/ui.mjs';
import {normalizedReviewDate} from './review_readiness.mjs';

const calendars = new WeakMap();
const isoDate = date => date.toISOString().slice(0, 10);
const asDate = value => new Date(`${value}T12:00:00Z`);

export function annotationCalendarDays(month) {
  const first = asDate(`${month}-01`);
  first.setUTCDate(1 - first.getUTCDay());
  return Array.from({length: 42}, (_, offset) => {
    const day = new Date(first);
    day.setUTCDate(first.getUTCDate() + offset);
    return isoDate(day);
  });
}

export function shiftAnnotationMonth(value, offset) {
  const date = asDate(value), day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + offset);
  const end = new Date(date);
  end.setUTCMonth(end.getUTCMonth() + 1, 0);
  date.setUTCDate(Math.min(day, end.getUTCDate()));
  return isoDate(date);
}

export function syncAnnotationCalendar(ui, root) {
  const control = ui.$('[data-annotation-date-picker]', root);
  if (!control?.querySelector) return;
  if (!calendars.has(control)) calendars.set(control, createCalendar(control, root));
  calendars.get(control).sync();
}

function createCalendar(control, root) {
  const input = control.querySelector('[name=truth_actual_date]');
  const toggle = control.querySelector('[data-annotation-calendar-toggle]');
  const panel = control.querySelector('[data-annotation-calendar]');
  const days = panel.querySelector('[data-annotation-calendar-days]');
  const year = panel.querySelector('[data-annotation-calendar-year]');
  const monthSelect = panel.querySelector('[data-annotation-calendar-month]');
  const requiredButton = panel.querySelector('[data-annotation-calendar-required]');
  const document = control.ownerDocument, window = document.defaultView;
  let month = '', focused = '', open = false, listeners;
  const requiredDate = () => normalizedReviewDate(root.querySelector('[name=truth_required_date]')?.value);
  const anchor = () => normalizedReviewDate(input.value) || requiredDate() || localToday();

  function close(focus = false) {
    if (!open) return;
    open = false;
    listeners?.abort();
    if (panel.hidePopover && panel.matches(':popover-open')) panel.hidePopover();
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (focus && !toggle.disabled) toggle.focus();
  }

  function position() {
    const bounds = input.getBoundingClientRect();
    const width = Math.min(304, window.innerWidth - 24);
    panel.style.width = `${width}px`;
    const height = panel.getBoundingClientRect().height;
    panel.style.left = `${Math.max(12, Math.min(bounds.left, window.innerWidth - width - 12))}px`;
    const below = bounds.bottom + 8;
    panel.style.top = `${below + height <= window.innerHeight - 12
      ? below : Math.max(12, bounds.top - height - 8)}px`;
  }

  function render(focusDay = false) {
    const currentYear = Number(month.slice(0, 4));
    year.innerHTML = Array.from({length: 100}, (_, index) => {
      const value = 2000 + index;
      return `<option value="${value}"${value === currentYear ? ' selected' : ''}>${value} 年</option>`;
    }).join('');
    monthSelect.value = String(Number(month.slice(5, 7)));
    const selected = normalizedReviewDate(input.value);
    days.innerHTML = annotationCalendarDays(month).map(value => {
      const date = asDate(value), inMonth = value.startsWith(month);
      const classes = [!inMonth && 'is-adjacent', value === selected && 'is-selected',
        value === localToday() && 'is-today'].filter(Boolean).join(' ');
      return `<button type="button" role="gridcell" data-annotation-calendar-date="${value}" class="${classes}" tabindex="${value === focused ? 0 : -1}" aria-label="${escapeHtml(`${date.getUTCFullYear()}年${date.getUTCMonth() + 1}月${date.getUTCDate()}日`)}" aria-selected="${value === selected}"${value === localToday() ? ' aria-current="date"' : ''}>${date.getUTCDate()}</button>`;
    }).join('');
    requiredButton.disabled = !requiredDate();
    requiredButton.title = requiredDate() ? `选择要求到货日期 ${requiredDate()}` : '请先填写要求到货日期';
    if (open) position();
    if (focusDay) days.querySelector(`[data-annotation-calendar-date="${focused}"]`)?.focus({preventScroll: true});
  }

  function choose(value) {
    input.value = value;
    input.dispatchEvent(new window.Event('input', {bubbles: true}));
    input.dispatchEvent(new window.Event('change', {bubbles: true}));
    close();
    input.focus();
  }

  function show() {
    if (input.disabled) return;
    focused = anchor();
    month = focused.slice(0, 7);
    panel.hidden = false;
    open = true;
    render();
    if (panel.showPopover) panel.showPopover();
    position();
    toggle.setAttribute('aria-expanded', 'true');
    listeners = new window.AbortController();
    const options = {signal: listeners.signal};
    document.addEventListener('pointerdown', event => {
      if (!control.contains(event.target)) close();
    }, options);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }, {...options, capture: true});
    root.closest('dialog')?.addEventListener('close', () => close(), options);
    const form = input.closest('.review-form');
    form?.addEventListener('scroll', () => {
      const bounds = input.getBoundingClientRect(), viewport = form.getBoundingClientRect();
      if (bounds.bottom < viewport.top || bounds.top > viewport.bottom) close();
      else position();
    }, options);
    window.addEventListener('resize', () => close(), options);
    days.querySelector(`[data-annotation-calendar-date="${focused}"]`)?.focus({preventScroll: true});
  }

  toggle.addEventListener('click', () => open ? close() : show());
  input.addEventListener('keydown', event => {
    if (event.altKey && event.key === 'ArrowDown') {
      event.preventDefault();
      show();
    }
  });
  input.addEventListener('blur', () => {
    const value = input.value.trim();
    if (!/^20\d{2}[-./]\d{1,2}[-./]\d{1,2}$/.test(value)) return;
    const normalized = normalizedReviewDate(value);
    if (normalized) input.value = normalized;
  });
  control.addEventListener('focusout', event => {
    // Rebuilding the day grid briefly removes its focused button. Only a
    // deliberate move to another control should dismiss the calendar.
    if (open && event.relatedTarget && !control.contains(event.relatedTarget)) close();
  });
  panel.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.dataset.annotationCalendarDate) return choose(button.dataset.annotationCalendarDate);
    if (button.hasAttribute('data-annotation-calendar-required')) return choose(requiredDate());
    if (button.hasAttribute('data-annotation-calendar-clear')) return choose('');
    const offset = button.hasAttribute('data-annotation-calendar-prev') ? -1
      : button.hasAttribute('data-annotation-calendar-next') ? 1 : 0;
    if (offset) {
      const next = shiftAnnotationMonth(`${month}-01`, offset);
      if (!next.startsWith('20')) return;
      month = next.slice(0, 7);
      focused = `${month}-01`;
      render();
    }
  });
  panel.addEventListener('input', event => event.stopPropagation());
  panel.addEventListener('change', event => {
    event.stopPropagation();
    month = `${year.value}-${String(monthSelect.value).padStart(2, '0')}`;
    focused = `${month}-01`;
    render();
    event.target.focus({preventScroll: true});
  });
  days.addEventListener('keydown', event => {
    const button = event.target.closest('[data-annotation-calendar-date]');
    if (!button) return;
    const value = button.dataset.annotationCalendarDate;
    const offsets = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7};
    let next = '';
    if (event.key in offsets) {
      const date = asDate(value);
      date.setUTCDate(date.getUTCDate() + offsets[event.key]);
      next = isoDate(date);
    } else if (event.key === 'PageUp' || event.key === 'PageDown') {
      next = shiftAnnotationMonth(value, (event.key === 'PageUp' ? -1 : 1) * (event.shiftKey ? 12 : 1));
    } else if (event.key === 'Home' || event.key === 'End') {
      const date = asDate(value);
      date.setUTCDate(date.getUTCDate() - date.getUTCDay() + (event.key === 'End' ? 6 : 0));
      next = isoDate(date);
    }
    if (!next || !next.startsWith('20')) return;
    event.preventDefault();
    focused = next;
    month = next.slice(0, 7);
    render(true);
  });
  return {sync() {
    toggle.disabled = input.disabled;
    if (input.disabled) close();
    if (open) render();
  }};
}
