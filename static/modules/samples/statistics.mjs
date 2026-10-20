import {escapeHtml as esc} from '../core/ui.mjs';

const PAGE_SIZE = 5;
const SCORED = new Set(['正确', '错误', '未识别', '失败']);
const BAD = new Set(['错误', '未识别', '失败']);
const emptyCell = row => ({state: row.job_status ? '未运行' : '未测试', value: '', decision: ''});
const sealCell = (row, key) => row.seals[key] || emptyCell(row);

export function summarizeCells(cells) {
  const counts = {};
  for (const cell of cells) counts[cell.state] = (counts[cell.state] || 0) + 1;
  const total = cells.filter(cell => SCORED.has(cell.state)).length;
  return {...counts, total, accuracy: total ? (counts['正确'] || 0) / total : null};
}

function disagrees(cells) {
  // Missing, failed and disabled channels have no decision to disagree with.
  const decisions = cells.filter(cell => SCORED.has(cell.state) && !['未识别', '失败'].includes(cell.state))
    .map(cell => cell.decision).filter(value => ['匹配', '不匹配', '部分匹配'].includes(value));
  return new Set(decisions).size > 1;
}

export function filterStatistics(rows, {company = '', tab = 'seal', filter = ''} = {}) {
  return rows.filter(row => {
    if (company && row.company !== company) return false;
    const cells = tab === 'date' ? [row.date] : Object.values(row.seals);
    if (filter === 'errors') return cells.some(cell => BAD.has(cell.state));
    if (filter === 'missing') return cells.some(cell => ['未识别', '失败'].includes(cell.state));
    if (filter === 'remote') return disagrees([sealCell(row, 'qingtong_template'), sealCell(row, 'qingtong_ocr')]);
    if (filter === 'local') return ['qingtong_template', 'qingtong_ocr'].some(key =>
      Object.entries(row.seals).some(([local, cell]) => local.startsWith('local:')
        && disagrees([sealCell(row, key), cell])));
    return true;
  });
}

export function statisticsCell(cell, showDecision = true) {
  const tone = cell.state === '正确' ? 'good' : BAD.has(cell.state) ? 'bad' : 'neutral';
  return `<span class="sample-stat-state ${tone}">${esc(cell.state)}</span>
    <span class="sample-stat-value">${esc(cell.value || (cell.state === '正确' ? '空白' : '—'))}</span>
    ${showDecision && cell.decision ? `<small>判定：${esc(cell.decision)}</small>` : ''}
    ${cell.note ? `<details><summary>说明</summary>${esc(cell.note)}</details>` : ''}`;
}

function card(label, stats, isSeal) {
  const percentage = stats.accuracy === null ? '—' : `${(stats.accuracy * 100).toFixed(1)}%`;
  return `<article class="sample-stat-card"><h3>${esc(label)}</h3>
    <p><strong>${percentage}</strong><span>${isSeal ? '判定正确率' : '日期正确率'}</span></p>
    <div>正确 ${stats['正确'] || 0} / 可评测 ${stats.total}</div>
    <footer>错误 ${stats['错误'] || 0} · 未识别 ${stats['未识别'] || 0} · 失败 ${stats['失败'] || 0}</footer>
    <small>待标注 ${stats['待标注'] || 0} · 未运行 ${stats['未运行'] || 0} · 测试中 ${stats['测试中'] || 0}</small></article>`;
}

function dateCards(stats) {
  const entries = [
    ['日期正确率', stats.accuracy === null ? '—' : `${(stats.accuracy * 100).toFixed(1)}%`,
      `正确 ${stats['正确'] || 0} / 可评测 ${stats.total}`],
    ['正确', stats['正确'] || 0, '识别日期与人工标注一致'],
    ['错误', stats['错误'] || 0, '识别日期与人工标注不一致'],
    ['未识别 / 失败', (stats['未识别'] || 0) + (stats['失败'] || 0),
      `未识别 ${stats['未识别'] || 0} · 失败 ${stats['失败'] || 0}`],
  ];
  return entries.map(([title, value, detail]) => `<article class="sample-stat-card"><h3>${title}</h3>
    <p><strong>${value}</strong></p><div>${detail}</div></article>`).join('');
}

export function createSampleStatistics({environment, ui, services, openResult, openOriginal, imageUrl}) {
  const {$, $$, toast} = ui;
  let data = null, company = '', taskId = '', tab = 'seal', filter = '', page = 1;
  let revision = 0, loading = false, rendered = '', knownCompanies = [];
  const dialog = () => $('#sample-statistics-dialog');

  function render() {
    if (!data) return;
    const scoped = filterStatistics(data.items, {company});
    const rows = filterStatistics(data.items, {company, tab, filter});
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.min(page, pages);
    const pending = scoped.filter(row => ['queued', 'running', 'awaiting_upload'].includes(row.job_status)).length;
    const tested = scoped.filter(row => ['succeeded', 'failed'].includes(row.job_status)).length;
    const failed = scoped.filter(row => row.job_status === 'failed').length;
    $('#sample-statistics-summary').textContent = `样单 ${scoped.length} · 已测试 ${tested} · 未测试 ${scoped.filter(row => !row.job_status).length} · 测试中 ${pending} · 运行失败 ${failed}`;
    $$('[data-stat-tab]').forEach(button => {
      const active = button.dataset.statTab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const columns = tab === 'seal' ? data.columns : [{key: 'date', label: '识别日期'}];
    const dateStats = summarizeCells(scoped.map(row => row.date));
    const filters = [['', '全部'], ['errors', tab === 'seal' ? '任一路错误' : '只看错误'],
      ...(tab === 'seal' ? [['remote', '清瞳两路不一致'], ['local', '清瞳与本地不一致']] : []),
      ['missing', '未识别 / 失败']];
    const markup = `<div class="sample-stat-cards">${tab === 'date' ? dateCards(dateStats) : columns.map(column => card(column.label,
      summarizeCells(scoped.map(row => sealCell(row, column.key))), true)).join('')}</div>
      ${tab === 'date' ? `<p class="sample-stat-date-note">待标注 ${dateStats['待标注'] || 0} · 未运行 ${dateStats['未运行'] || 0} · 测试中 ${dateStats['测试中'] || 0}，不计入正确率</p>` : ''}
      <div class="sample-stat-filters" role="group" aria-label="筛选统计结果">${filters.map(([key, label]) =>
        `<button type="button" data-stat-filter="${key}" class="secondary-button${filter === key ? ' active' : ''}" aria-pressed="${filter === key}">${label}</button>`).join('')}</div>
      <div class="sample-stat-table-wrap"><table class="sample-stat-table">
      <thead><tr><th>样单 / 客户公司</th><th>${tab === 'seal' ? '签章要求 / 标注结论' : '标注日期'}</th>
      ${columns.map(column => `<th>${esc(column.label)}</th>`).join('')}<th>操作</th></tr></thead>
      <tbody>${rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map(row =>
        `<tr><td><strong>${esc(row.filename)}</strong><small>${esc(row.company)}</small></td>
        <td>${esc(tab === 'seal' ? row.requirement || '未标注签章要求' : row.expected_date === null ? '待标注' : row.expected_date || '空白')}
        ${tab === 'seal' ? `<small>标注：${esc(row.expected_seal ? `应${row.expected_seal}` : '待标注')}</small>` : ''}</td>
        ${columns.map(column => `<td>${statisticsCell(tab === 'date' ? row.date : sealCell(row, column.key), tab === 'seal')}</td>`).join('')}
        <td><button type="button" class="text-button" data-stat-view="${esc(row.filename)}">${row.result_id ? '查看' : '预览'} ›</button></td></tr>`).join('')
        || `<tr><td colspan="${columns.length + 3}" class="empty-state">当前范围没有符合条件的样单</td></tr>`}</tbody></table></div>`;
    if (markup !== rendered) {
      // Preserve keyboard focus during automatic status refresh.
      const focusedFilter = environment.document.activeElement?.dataset?.statFilter;
      $('#sample-statistics-body').innerHTML = markup;
      rendered = markup;
      if (focusedFilter !== undefined) $$('[data-stat-filter]').find(button => button.dataset.statFilter === focusedFilter)?.focus();
    }
    $('#sample-statistics-note').textContent = tab === 'seal'
      ? '按人工标注的“应匹配 / 应不匹配”评估各路判定，不代表印章逐字转写准确率。未识别、失败计入分母；未测试、未运行、测试中、待标注不计入。'
      : '识别日期与人工标注日期比较，标注为空白且未读出日期也算正确。未识别、失败计入分母；未测试、未运行、测试中、待标注不计入。';
    $('#sample-statistics-page').textContent = `共 ${rows.length} 张 · ${page} / ${pages}`;
    $('#sample-statistics-prev').disabled = page <= 1;
    $('#sample-statistics-next').disabled = page >= pages;
  }

  async function refresh({reset = false} = {}) {
    if (!dialog().open || (loading && !reset)) return;
    const token = ++revision;
    loading = true;
    if (reset) {
      data = null; rendered = '';
      $('#sample-statistics-body').innerHTML = '<p class="empty-state">正在加载统计结果…</p>';
      $('#sample-statistics-summary').textContent = '';
    }
    $('#sample-statistics-feedback').textContent = '';
    try {
      const result = await services.samples.statistics(taskId);
      if (token !== revision || !dialog().open) return;
      data = result;
      const select = $('#sample-statistics-batch');
      const options = '<option value="">每张样单最近一次测试</option>' + data.batches.map(batch =>
        `<option value="${esc(batch.id)}">${esc(batch.created_at.replace('T', ' ').replace(/[+-]\d{2}:\d{2}$/, ''))} · ${batch.total} 张</option>`).join('');
      if (select.innerHTML !== options) { select.innerHTML = options; select.value = taskId; }
      render();
    } catch (error) {
      if (token !== revision || !dialog().open) return;
      $('#sample-statistics-feedback').textContent = `统计加载失败：${error.message}。请点击刷新重试。`;
      if (!data) $('#sample-statistics-body').innerHTML = '';
    } finally { if (token === revision) loading = false; }
  }

  function show(context) {
    company = context.company; knownCompanies = context.companies;
    page = 1; filter = ''; taskId = '';
    $('#sample-statistics-batch').value = '';
    $('#sample-statistics-company').innerHTML = '<option value="">全部公司</option>' +
      knownCompanies.map(row => `<option value="${esc(row.name)}">${esc(row.name)}</option>`).join('');
    $('#sample-statistics-company').value = company;
    if (!dialog().open) dialog().showModal();
    return refresh({reset: true});
  }

  function initialize() {
    $('#sample-statistics-company').addEventListener('change', event => { company = event.target.value; page = 1; render(); });
    $('#sample-statistics-batch').addEventListener('change', event => { taskId = event.target.value; page = 1; refresh({reset: true}); });
    $('#sample-statistics-refresh').addEventListener('click', () => refresh());
    $$('[data-stat-tab]').forEach(button => button.addEventListener('click', () => {
      tab = button.dataset.statTab; filter = ''; page = 1; render();
    }));
    $('#sample-statistics-prev').addEventListener('click', () => { page--; render(); });
    $('#sample-statistics-next').addEventListener('click', () => { page++; render(); });
    $('#sample-statistics-body').addEventListener('click', async event => {
      const chip = event.target.closest('[data-stat-filter]');
      if (chip) { filter = chip.dataset.statFilter; page = 1; render(); return; }
      const button = event.target.closest('[data-stat-view]');
      if (!button || !data) return;
      const row = data.items.find(item => item.filename === button.dataset.statView);
      if (!row) return;
      dialog().close();
      try {
        if (row.result_id) await openResult(row.result_id);
        else openOriginal({filename: row.filename, sample_center: true, preview_url: imageUrl(row.filename)},
          $('#sample-statistics-open'));
      } catch (error) { toast(error.message, 'danger'); }
    });
    dialog().addEventListener('close', () => { revision++; loading = false; });
    environment.window.addEventListener('hashchange', () => dialog().close());
  }
  return {initialize, show, refresh};
}
