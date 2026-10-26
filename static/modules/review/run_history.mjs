import {escapeHtml} from '../core/ui.mjs';
import {renderRunHistory} from './run_history_view.mjs';

/** A read-only panel beside the original image; the existing edit form stays intact. */
export function createReviewRunHistory({environment, ui, services}) {
  const {$} = ui;
  const panel = () => $('#review-runs-panel');
  const body = () => $('#review-runs-body');
  const button = () => $('#review-runs-open');
  let context = null, data = null, request = 0, opened = false, loading = false, formState = null;
  const boundPanels = new WeakSet();

  function restoreForm() {
    if (!formState) return;
    formState.form.hidden = formState.hidden;
    formState.form.inert = formState.inert;
    formState.form.scrollTop = formState.scrollTop;
    formState.form.scrollLeft = formState.scrollLeft;
    formState = null;
  }

  function close({restoreFocus = true} = {}) {
    const wasOpen = opened;
    opened = false; loading = false; ++request;
    if (panel()) panel().hidden = true;
    $('.review-layout')?.classList?.remove('run-history-open');
    button()?.setAttribute?.('aria-expanded', 'false');
    restoreForm();
    if (wasOpen && restoreFocus) button()?.focus?.({preventScroll:true});
  }

  function clear() {
    close({restoreFocus:false});
    context = null; data = null;
    if (button()) button().hidden = true;
    const count = $('[data-run-count]');
    if (count) { count.textContent = ''; count.hidden = true; }
    if (body()) body().innerHTML = '';
  }

  function setContext(item, {sample = false} = {}) {
    clear();
    const id = item?.id || item?.result_id;
    if (!id || !button() || !panel()) return;
    context = {id, sample, filename:item.filename || ''};
    button().hidden = false;
    button().disabled = false;
    if ($('#review-runs-title')) $('#review-runs-title').textContent = '本次识别明细';
    if (!boundPanels.has(panel())) {
      panel().addEventListener?.('click', handlePanelClick);
      boundPanels.add(panel());
    }
  }

  function expanded(selector, attribute) {
    return new Set([...(body()?.querySelectorAll?.(selector) || [])].filter(node => node.open).map(node => node.getAttribute(attribute)));
  }

  function render({preserve = true, error = ''} = {}) {
    if (!body() || !data) return;
    const top = body().scrollTop || 0;
    const openKeys = preserve ? expanded('[data-call-group]', 'data-call-group') : undefined;
    const imageKeys = expanded('[data-call-image]', 'data-call-image');
    const explanationOpen = expanded('[data-call-explanation]', 'data-call-explanation').has('count');
    body().innerHTML = renderRunHistory(data, {openKeys, imageKeys, explanationOpen, loading, error});
    body().scrollTop = top;
    const count = $('[data-run-count]');
    if (count) { count.textContent = data.available === true ? `${data.summary?.total || 0} 次` : '—'; count.hidden = false; }
  }

  async function load() {
    if (!opened || !context || loading) return;
    const entry = context, serial = ++request;
    loading = true;
    if (data) render();
    else body().innerHTML = '<div class="empty-state" role="status">正在读取本次识别明细…</div>';
    const current = () => opened && context === entry && serial === request;
    try {
      const service = services[entry.sample ? 'samples' : 'records'];
      if (!service?.recognitionCalls) throw new Error('暂时无法读取识别调用明细');
      const result = await service.recognitionCalls(entry.id);
      if (!current()) return;
      const hadGroups = data?.available === true && Array.isArray(data.calls) && data.calls.length > 0;
      data = {...result, calls:result.calls || []};
      loading = false;
      render({preserve:hadGroups});
    } catch (error) {
      if (!current()) return;
      loading = false;
      if (data) render({error:`读取失败：${error.message}`});
      else body().innerHTML = `<div class="empty-state" role="alert"><p>本次识别明细加载失败：${escapeHtml(error.message)}</p><button type="button" class="secondary-button" data-runs-refresh>重试</button></div>`;
    }
  }

  async function open() {
    if (!context || !panel() || !body()) return;
    if (opened) { close(); return; }
    const form = $('#review-form');
    if (form) formState = {form, hidden:form.hidden, inert:form.inert,
      scrollTop:form.scrollTop || 0, scrollLeft:form.scrollLeft || 0};
    opened = true;
    panel().hidden = false;
    $('.review-layout')?.classList?.add('run-history-open');
    if (form) { form.hidden = true; form.inert = true; }
    button()?.setAttribute?.('aria-expanded', 'true');
    $('[data-runs-close]', panel())?.focus?.({preventScroll:true});
    if (data) render();
    else await load();
  }

  async function handlePanelClick(event) {
    if (event.target.closest?.('[data-runs-close]')) { close(); return; }
    if (event.target.closest?.('[data-runs-refresh]')) { await load(); return; }
  }

  function initialize() {
    button()?.addEventListener?.('click', open);
    environment.document?.addEventListener?.('keydown', event => {
      if (!opened || event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation?.(); event.stopPropagation?.();
      close();
    }, true);
    $('#review-dialog')?.addEventListener?.('cancel', event => {
      if (!opened) return;
      event.preventDefault(); event.stopImmediatePropagation?.(); event.stopPropagation?.();
      close();
    }, true);
    $('#review-dialog')?.addEventListener?.('close', () => {
      // A queued native close event can belong to a previous viewing.
      if (!$('#review-dialog')?.open) clear();
    });
  }

  return {initialize, setContext, open, close, clear, isOpen:() => opened};
}
