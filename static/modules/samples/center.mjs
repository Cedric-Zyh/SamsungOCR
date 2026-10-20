import {escapeHtml} from '../core/ui.mjs';
import {createSampleStatistics} from './statistics.mjs';

const PAGE_SIZE = 6;
export const sampleImageUrl = (name, thumbnail = false) =>
  `/api/samples/image/${encodeURIComponent(name)}${thumbnail ? '?thumbnail=1' : ''}`;

export function sampleScope(items, {company = '', search = '', status = ''} = {}) {
  const query = search.trim().toLocaleLowerCase();
  return items.filter(item => (!company || item.company === company)
    && (!query || item.filename.toLocaleLowerCase().includes(query)) && (!status || item.status === status));
}

export function sampleCard(item, selected) {
  const filename = escapeHtml(item.filename), busy = item.status === '测试中';
  const checks = [['日期', item.checks?.date], ['印章', item.checks?.seal], ['签名', item.signature]];
  const detail = item.status === '已完成'
    ? `<p class="sample-card-checks">${checks.map(([label, value]) => `<span data-match="${value === '匹配' ? 'true' : value === '未执行' ? '' : 'false'}">${label} ${escapeHtml(value || '未执行')}</span>`).join('')}</p>`
    : `<p class="${item.error ? 'sample-card-error' : ''}">${escapeHtml(item.error || (busy ? item.job_status === 'queued' ? '等待测试…' : '正在识别…' : '尚无测试结果'))}</p>`;
  return `<article class="sample-card${selected ? ' is-selected' : ''}" data-filename="${filename}">
    <div class="sample-card-image"><button class="sample-thumbnail" type="button" data-sample-preview aria-label="预览 ${filename}"><img src="${sampleImageUrl(item.filename, true)}" loading="lazy" alt="${filename} 样单缩略图"></button><label><input type="checkbox" data-sample-select aria-label="选择 ${filename}" ${selected ? 'checked' : ''} ${busy ? 'disabled' : ''}></label><span class="sample-state" data-status="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></div>
    <div class="sample-card-copy"><h3>${filename}</h3>${detail}
    <div class="sample-card-actions"><button type="button" data-sample-preview>预览</button><button type="button" data-sample-result ${item.result_id ? '' : 'disabled'}>测试结果</button><button type="button" data-sample-run ${busy ? 'disabled' : ''}>${busy ? '测试中…' : item.result_id || item.status === '运行失败' ? '重新测试' : '运行测试'}</button></div></div></article>`;
}

export function createSampleCenter({environment, ui, services, flushSettings, openResult, openOriginal}) {
  const {$, $$, toast} = ui;
  let items = [], companies = [], company = '', status = '', page = 1;
  let selected = new Set(), loading = false, running = false, loaded = false, lastRender = '';
  let historyLoading = false, historyMarkup = '';
  const statistics = createSampleStatistics({environment, ui, services, openResult, openOriginal, imageUrl: sampleImageUrl});
  const scope = () => sampleScope(items, {company, search: $('#sample-search').value, status});
  const companyItems = () => sampleScope(items, {company});
  const visible = () => scope().slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function renderCompanies() {
    const query = $('#sample-company-search').value.trim().toLocaleLowerCase();
    const rows = companies.filter(row => row.name.toLocaleLowerCase().includes(query));
    const markup = [{name: '', count: items.length}, ...rows].map(row => `<button type="button" data-company="${escapeHtml(row.name)}" class="${company === row.name ? 'active' : ''}" aria-pressed="${company === row.name}"><span>${escapeHtml(row.name || '全部样单')}</span><small>${row.count}</small></button>`).join('');
    $('#sample-company-list').innerHTML = markup + (query && !rows.length ? '<p class="empty-state">没有找到该公司</p>' : '');
    $('#sample-company-count').textContent = companies.length;
  }

  function syncSelection() {
    const available = visible().filter(item => item.status !== '测试中');
    $('#sample-select-page').checked = available.length > 0 && available.every(item => selected.has(item.filename));
    $('#sample-select-page').indeterminate = available.some(item => selected.has(item.filename)) && !$('#sample-select-page').checked;
    $('#sample-select-page').disabled = !available.length || running;
    $('#sample-run-selected').disabled = running || !selected.size;
    $('#sample-clear-selection').textContent = `已选 ${selected.size} 张${selected.size ? ' · 清空' : ''}`;
    $('#sample-clear-selection').disabled = !selected.size;
    $('#sample-run-company').disabled = running || !companyItems().some(item => item.status !== '测试中');
    $$('#sample-grid [data-sample-select]').forEach(input => {
      input.checked = selected.has(input.closest('[data-filename]').dataset.filename);
      input.closest('.sample-card').classList.toggle('is-selected', input.checked);
    });
    $$('#sample-grid [data-sample-run]').forEach(button => {
      const item = items.find(row => row.filename === button.closest('[data-filename]').dataset.filename);
      button.disabled = running || item.status === '测试中';
    });
  }

  function render() {
    const rows = scope(), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.min(page, pages);
    $('#sample-company-title').textContent = company || '全部样单';
    $('#sample-library-summary').textContent = `${companyItems().length} 张样单 · 使用当前配置`;
    $('#sample-run-company').textContent = company ? '▶ 运行本公司全部' : '▶ 运行全部样单';
    const markup = visible().map(item => sampleCard(item, selected.has(item.filename))).join('') ||
      `<div class="empty-state">${items.length ? '当前条件下没有样单，请调整搜索或筛选。' : '暂无已标注样单，请检查样单图片与标注文件。'}</div>`;
    // Polling must not replace focused controls just because selection changed.
    const renderKey = JSON.stringify(visible()) + Boolean(items.length);
    if (renderKey !== lastRender) { $('#sample-grid').innerHTML = markup; lastRender = renderKey; }
    $('#sample-page-info').textContent = rows.length ? `显示 ${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, rows.length)}，共 ${rows.length} 张` : '共 0 张';
    $('#sample-page-number').textContent = `${page} / ${pages}`;
    $('#sample-prev').disabled = page <= 1;
    $('#sample-next').disabled = page >= pages;
    $$('[data-sample-status]').forEach(button => {
      const active = button.dataset.sampleStatus === status;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    syncSelection();
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await services.samples.list();
      const changed = JSON.stringify(companies) !== JSON.stringify(data.companies);
      items = data.items; companies = data.companies;
      if (company && !companies.some(row => row.name === company)) company = '';
      selected = new Set([...selected].filter(name => items.some(item => item.filename === name && item.status !== '测试中')));
      if (!loaded || changed) renderCompanies();
      loaded = true;
      $('#sample-feedback').textContent = '';
      render();
      if ($('#sample-history-dialog').open) await refreshHistory();
      await statistics.refresh();
    } catch (error) {
      $('#sample-feedback').textContent = `加载失败：${error.message}，正在等待恢复连接。`;
      if (!loaded) $('#sample-grid').innerHTML = '<div class="empty-state">样单暂时无法加载</div>';
    } finally { loading = false; }
  }

  async function run(names) {
    if (running) { toast('正在提交测试，请稍候再操作'); return false; }
    if (!names.length) return false;
    running = true; syncSelection();
    try {
      await flushSettings();
      const data = await services.samples.run(names);
      const accepted = new Set(data.accepted);
      for (const item of items) if (accepted.has(item.filename)) {
        item.status = '测试中'; item.job_status = 'queued'; item.error = '';
        selected.delete(item.filename);
      }
      render();
      toast(data.accepted.length ? `已开始 ${data.accepted.length} 张样单测试${data.skipped.length ? `，${data.skipped.length} 张已在测试中` : ''}` : '所选样单已在测试中', 'success');
      await load();
      return true;
    } catch (error) { toast(error.message, 'danger'); return false; }
    finally { running = false; syncSelection(); }
  }

  async function refreshHistory() {
    const dialog = $('#sample-history-dialog'), body = $('#sample-history-body');
    if (historyLoading || !dialog.open) return;
    historyLoading = true;
    try {
      const rows = await services.samples.history();
      if (!dialog.open) return;
      const states = {queued: '等待测试', running: '测试中', succeeded: '已完成', failed: '运行失败', cancelled: '已取消'};
      const markup = rows.map(row => `<div class="sample-history-row"><div><strong>${escapeHtml(row.filename)}</strong><small>${escapeHtml(row.created_at)}${row.error_message ? ` · ${escapeHtml(row.error_message)}` : ''}</small></div><span>${states[row.status] || '未测试'}</span><button type="button" class="text-button" data-history-result="${row.result_id || ''}" ${row.result_id ? '' : 'disabled'}>测试结果</button></div>`).join('') || '<p class="empty-state">暂无测试记录</p>';
      if (markup !== historyMarkup) { body.innerHTML = markup; historyMarkup = markup; }
    } catch (error) {
      historyMarkup = '';
      body.innerHTML = `<p role="alert">${escapeHtml(error.message)}</p>`;
    } finally { historyLoading = false; }
  }

  async function showHistory() {
    const dialog = $('#sample-history-dialog');
    historyMarkup = '';
    $('#sample-history-body').innerHTML = '<p>正在加载测试记录…</p>';
    if (!dialog.open) dialog.showModal();
    await refreshHistory();
  }

  function initialize() {
    statistics.initialize();
    const renderFromTop = () => { render(); $('#sample-grid').scrollTop = 0; };
    $('#sample-company-search').addEventListener('input', renderCompanies);
    $('#sample-company-list').addEventListener('click', event => {
      const button = event.target.closest('[data-company]');
      if (!button) return;
      company = button.dataset.company; page = 1; selected.clear();
      renderCompanies(); renderFromTop();
    });
    $('#sample-search').addEventListener('input', () => { page = 1; renderFromTop(); });
    $$('[data-sample-status]').forEach(button => button.addEventListener('click', () => {
      status = button.dataset.sampleStatus; page = 1; renderFromTop();
    }));
    $('#sample-prev').addEventListener('click', () => { page--; renderFromTop(); });
    $('#sample-next').addEventListener('click', () => { page++; renderFromTop(); });
    $('#sample-clear-selection').addEventListener('click', () => { selected.clear(); syncSelection(); });
    $('#sample-select-page').addEventListener('change', event => {
      visible().filter(item => item.status !== '测试中').forEach(item =>
        event.target.checked ? selected.add(item.filename) : selected.delete(item.filename));
      syncSelection();
    });
    $('#sample-grid').addEventListener('change', event => {
      if (!event.target.matches('[data-sample-select]')) return;
      const name = event.target.closest('[data-filename]').dataset.filename;
      event.target.checked ? selected.add(name) : selected.delete(name); syncSelection();
    });
    $('#sample-grid').addEventListener('click', async event => {
      const card = event.target.closest('[data-filename]');
      if (!card) return;
      const item = items.find(row => row.filename === card.dataset.filename);
      try {
        if (event.target.closest('[data-sample-run]')) await run([item.filename]);
        else if (event.target.closest('[data-sample-result]')) await openResult(item.result_id);
        else if (event.target.closest('[data-sample-preview]')) {
          if (item.result_id) await openResult(item.result_id, {preview: true});
          else openOriginal({filename: item.filename, sample_center: true, preview_url: sampleImageUrl(item.filename)}, event.target.closest('button'));
        }
      } catch (error) { toast(error.message, 'danger'); }
    });
    $('#sample-run-selected').addEventListener('click', () => run([...selected]));
    $('#sample-run-company').addEventListener('click', () => run(companyItems().map(item => item.filename)));
    $('#sample-history-open').addEventListener('click', showHistory);
    $('#sample-statistics-open').addEventListener('click', () => statistics.show({company, companies}));
    $('#sample-history-refresh').addEventListener('click', refreshHistory);
    $('#sample-history-body').addEventListener('click', async event => {
      const button = event.target.closest('[data-history-result]');
      if (!button?.dataset.historyResult) return;
      $('#sample-history-dialog').close();
      try { await openResult(Number(button.dataset.historyResult)); } catch (error) { toast(error.message, 'danger'); }
    });
    environment.window.addEventListener('hashchange', () => $('#sample-history-dialog').close());
  }
  return {initialize, load, run};
}
