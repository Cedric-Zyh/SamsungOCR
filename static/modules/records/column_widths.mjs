export const COLUMN_WIDTHS_KEY = 'receipt.records.column-widths.v1';
const LIMITS = [[34,100], [120,1200], [170,900], [88,500], [160,900], [90,500], [90,400]];
const LABELS = ['选择', '订单 / 客户', '日期核对', '客户印章', '签名核对', '处理结果', '操作'];

export function clampColumnWidth(index, width) {
  return Math.round(Math.min(LIMITS[index][1], Math.max(LIMITS[index][0], width)));
}

export function readColumnWidths(storage) {
  try {
    const saved = JSON.parse(storage?.getItem(COLUMN_WIDTHS_KEY) || 'null');
    if (saved?.version !== 1 || !Array.isArray(saved.widths) || saved.widths.length !== LIMITS.length) return null;
    if (!saved.widths.every((width,index) => typeof width === 'number' && Number.isFinite(width) && width >= LIMITS[index][0] && width <= LIMITS[index][1])) return null;
    return saved.widths.map(Math.round);
  } catch (_) { return null; }
}

/** Widths are local preferences; editing always starts locked after a reload. */
export function initializeColumnWidths({environment, ui}) {
  const {$} = ui, {document, window, localStorage} = environment;
  const table = $('#records-table'), toggle = $('#records-column-lock'), reset = $('#records-column-reset');
  if (!table?.querySelectorAll || !toggle || !reset || table.dataset.columnResizeReady) return;
  const columns = [...table.querySelectorAll('col')], headers = [...table.querySelectorAll('thead th')];
  if (columns.length !== LIMITS.length || headers.length !== LIMITS.length) return;
  table.dataset.columnResizeReady = 'true';
  let widths = readColumnWidths(localStorage), unlocked = false, drag = null;
  const handles = [];
  const announce = message => { $('#records-column-status').textContent = message; };
  const measure = () => headers.map((header,index) => clampColumnWidth(index, header.getBoundingClientRect().width));

  function apply(next) {
    widths = next ? [...next] : null;
    columns.forEach((column,index) => { column.style.width = widths ? `${widths[index]}px` : ''; });
    const total = widths ? `${widths.reduce((sum,width) => sum+width, 0)}px` : '';
    table.style.width = total;
    table.style.minWidth = total;
    table.classList.toggle('has-custom-column-widths', !!widths);
    handles.forEach((handle,index) => handle.setAttribute('aria-valuenow', String(widths?.[index] || measure()[index])));
  }

  function persist() {
    try {
      if (widths) localStorage?.setItem(COLUMN_WIDTHS_KEY, JSON.stringify({version:1,widths}));
      else localStorage?.removeItem(COLUMN_WIDTHS_KEY);
    } catch (_) { announce('列宽已调整，本次窗口内有效。'); }
  }

  function finish(cancel=false) {
    if (!drag) return;
    const previous = drag;
    drag = null;
    document.body.classList.remove('records-column-resizing');
    if (cancel) apply(previous.before);
    else if (previous.changed) persist();
    if (previous.handle.hasPointerCapture?.(previous.pointerId)) previous.handle.releasePointerCapture(previous.pointerId);
  }

  function setUnlocked(value) {
    finish();
    unlocked = value;
    table.classList.toggle('columns-unlocked', unlocked);
    toggle.setAttribute('aria-pressed', String(unlocked));
    toggle.title = unlocked ? '锁定当前列宽，停止调整' : '解锁后拖动表头分隔线调整列宽';
    $('[data-column-lock-label]').textContent = unlocked ? '调整列宽' : '列宽已锁定';
    $('[data-column-lock-shackle]').setAttribute('d', unlocked ? 'M8 10V6a4 4 0 0 1 7.5-2' : 'M8 10V6a4 4 0 0 1 8 0v4');
    reset.classList.toggle('hidden', !unlocked);
    handles.forEach((handle,index) => {
      handle.hidden = !unlocked;
      handle.tabIndex = unlocked ? 0 : -1;
      handle.setAttribute('aria-valuenow', String(widths?.[index] || measure()[index]));
    });
  }

  headers.forEach((header,index) => {
    const handle = document.createElement('span');
    handle.className = 'record-column-resize';
    handle.setAttribute('role','separator');
    handle.setAttribute('aria-orientation','vertical');
    handle.setAttribute('aria-label',`调整${LABELS[index]}列宽`);
    handle.setAttribute('aria-valuemin',String(LIMITS[index][0]));
    handle.setAttribute('aria-valuemax',String(LIMITS[index][1]));
    handle.title = `拖动调整${LABELS[index]}列宽，也可用左右方向键`;
    header.append(handle); handles.push(handle);
    handle.addEventListener('click',event => event.stopPropagation());
    handle.addEventListener('pointerdown',event => {
      if (!unlocked || event.button !== 0 || drag) return;
      event.preventDefault(); event.stopPropagation();
      drag = {index, handle, pointerId:event.pointerId, x:event.clientX, base:widths ? [...widths] : measure(), before:widths ? [...widths] : null, changed:false};
      handle.setPointerCapture(event.pointerId);
      document.body.classList.add('records-column-resizing');
    });
    handle.addEventListener('pointermove',event => {
      if (!drag || drag.pointerId !== event.pointerId || drag.handle !== handle) return;
      const next = [...drag.base];
      next[index] = clampColumnWidth(index, drag.base[index] + event.clientX - drag.x);
      if (next[index] === drag.base[index] && !drag.changed) return;
      drag.changed = true; apply(next);
    });
    handle.addEventListener('pointerup',event => { if (drag?.pointerId === event.pointerId) finish(); });
    handle.addEventListener('pointercancel',() => finish(true));
    handle.addEventListener('lostpointercapture',() => finish());
    handle.addEventListener('keydown',event => {
      if (!unlocked || !['ArrowLeft','ArrowRight'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const next = widths ? [...widths] : measure();
      next[index] = clampColumnWidth(index, next[index] + (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 40 : 10));
      apply(next); persist();
    });
  });
  apply(widths); setUnlocked(false);
  toggle.addEventListener('click',() => {
    setUnlocked(!unlocked);
    announce(unlocked ? '列宽已解锁，拖动表头分隔线调整，调整后自动保存。' : '列宽已锁定。');
  });
  reset.addEventListener('click',() => {
    if (!unlocked) return;
    finish(true); apply(null); persist();
    announce('已恢复默认列宽。');
  });
  document.addEventListener('keydown',event => {
    if (event.key === 'Escape' && drag) { event.preventDefault(); finish(true); }
  });
  window.addEventListener('blur',() => finish());
  window.addEventListener('hashchange',() => setUnlocked(false));
}
