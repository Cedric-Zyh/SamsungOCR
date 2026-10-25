import {escapeHtml as esc} from '../core/ui.mjs';
import {rowAnnotated} from './statistics_metrics.mjs';
import {statisticsOverview, statisticsScopeNote, statisticsScopeSummary, statisticsSummary, statTime} from './statistics_view.mjs';
import {createStatisticsPreview} from './statistics_preview.mjs';

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

export function filterStatistics(rows, {company = '', tab = 'seal', filter = '', channel = '', requirement = ''} = {}) {
  return rows.filter(row => {
    if (company && row.company !== company) return false;
    if (requirement && (row.requirement || '').trim() !== requirement) return false;
    const cells = tab === 'date' ? [row.date] : channel ? [sealCell(row, channel)] : Object.values(row.seals);
    if (filter === 'pending_truth') return !rowAnnotated(row, tab);
    if (filter === 'untested') return !row.job_status;
    if (filter === 'errors') return cells.some(cell => BAD.has(cell.state));
    if (filter === 'missing') return cells.some(cell => ['未识别', '失败'].includes(cell.state));
    if (filter === 'wrong') return cells.some(cell => cell.state === '错误');
    if (filter === 'unrecognized') return cells.some(cell => cell.state === '未识别');
    if (filter === 'failed') return cells.some(cell => cell.state === '失败');
    if (filter === 'false_accept') return row.expected_seal === '不匹配' && cells.some(cell => cell.state === '错误' && cell.decision === '匹配');
    if (filter === 'false_reject') return row.expected_seal === '匹配' && cells.some(cell => cell.state === '错误' && cell.decision === '不匹配');
    if (filter === 'partial') return cells.some(cell => cell.state === '错误' && cell.decision === '部分匹配');
    if (filter === 'other') return cells.some(cell => cell.state === '错误' && cell.decision !== '部分匹配'
      && !(row.expected_seal === '不匹配' && cell.decision === '匹配') && !(row.expected_seal === '匹配' && cell.decision === '不匹配'));
    if (filter === 'wrong_date') return Boolean(row.expected_date) && row.date.state === '错误' && Boolean(row.date.value);
    if (filter === 'date_missed') return Boolean(row.expected_date) && (row.date.state === '未识别' || (row.date.state === '错误' && !row.date.value));
    if (filter === 'date_spurious') return row.expected_date === '' && row.date.state === '错误' && Boolean(row.date.value);
    if (filter === 'date_with_value') return Boolean(row.expected_date);
    if (filter === 'date_blank') return row.expected_date === '';
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
    <span class="sample-stat-value" title="${esc(cell.value || '')}">${esc(cell.value || (cell.state === '正确' ? '空白' : '—'))}</span>
    ${showDecision && cell.decision ? `<small>判定：${esc(cell.decision)}</small>` : ''}
    ${cell.note ? `<details><summary>说明</summary>${esc(cell.note)}</details>` : ''}`;
}

export function statisticsDistribution(stats) {
  if (!stats.total) return '<div class="sample-stat-distribution-empty">暂无可评测结果</div>';
  const parts = [['正确', 'good'], ['错误', 'bad'], ['未识别', 'missing'], ['失败', 'failed']];
  return `<div class="sample-stat-distribution" role="img" aria-label="${parts.map(([state]) => `${state} ${stats[state] || 0}`).join('，')}">${parts.map(([state, tone]) => {
    const count = stats[state] || 0;
    return count ? `<span class="${tone}" style="width:${count / stats.total * 100}%" title="${state} ${count} · ${(count / stats.total * 100).toFixed(1)}%"></span>` : '';
  }).join('')}</div>`;
}

export function createSampleStatistics({environment, ui, services, openResult, openOriginal, imageUrl}) {
  const {$, $$, toast} = ui;
  let data = null, company = '', taskId = '', tab = 'seal', filter = '', page = 1;
  let section = 'overview';
  let channel = '', requirement = '', groupBy = 'company', showAllGroups = false;
  const typeFilters = {date: '', seal: ''};
  let revision = 0, loading = false, rendered = '', knownCompanies = [];
  let exporting = false;
  let refreshDeferred = false;
  const expandedAnalysis = new Map();
  const dialog = () => $('#sample-statistics-dialog');
  const preview = createStatisticsPreview({environment, ui, openResult, openOriginal, imageUrl,
    onReturn: () => { if (refreshDeferred) { refreshDeferred = false; refresh(); } },
  });
  const syncExport = () => {
    $('#sample-statistics-export').disabled = !data || loading || exporting;
    $('#sample-statistics-scope-mode').disabled = loading;
    $('#sample-statistics-batch').disabled = loading;
  };

  function render(resetTableScroll = false) {
    $$('[data-stat-analysis]', $('#sample-statistics-body')).forEach(panel =>
      expandedAnalysis.set(`${dialog().dataset.statTab || tab}:${panel.dataset.statAnalysis}`, panel.open));
    dialog().dataset.statTab = tab;
    dialog().dataset.section = section;
    $$('[data-stat-section]').forEach(button => {
      const active = button.dataset.statSection === section;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.setAttribute('tabindex', active ? '0' : '-1');
    });
    $('#sample-statistics-body').setAttribute('aria-labelledby', `sample-statistics-${section === 'overview' ? 'overview' : 'details'}-tab`);
    $('#sample-statistics-footer').hidden = section !== 'details' || !data;
    $('#sample-statistics-show-details').hidden = section !== 'overview';
    $('#sample-statistics-pagination').hidden = section !== 'details' || !data;
    $('#sample-statistics-export').title = section === 'overview'
      ? '导出当前公司和测试范围的全部汇总及明细' : '导出当前筛选条件下的全部汇总及明细';
    if (!data) return;
    const scoped = filterStatistics(data.items, {company});
    const rows = filterStatistics(data.items, {company, tab, filter, channel, requirement});
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.min(page, pages);
    $('#sample-statistics-summary').innerHTML = statisticsSummary(scoped, tab);
    $('#sample-statistics-scope-summary').textContent = statisticsScopeSummary(data, scoped, taskId);
    $('#sample-statistics-scope-note').textContent = statisticsScopeNote(data, scoped, taskId);
    $$('[data-stat-tab]').forEach(button => {
      const active = button.dataset.statTab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const columns = tab === 'seal' ? data.columns : [{key: 'date', label: '识别日期'}];
    const filters = [['', '全部'], ['errors', channel || tab === 'date' ? '异常结果' : '任一路异常'],
      ...(tab === 'seal' ? [['false_accept','误放行'], ['false_reject','误拒绝'], ['partial','部分匹配'], ['other','其他判定错误'], ['remote', '清瞳两路不一致'], ['local', '清瞳与本地不一致']]
        : [['wrong_date','日期读错'], ['date_missed','漏读日期'], ['date_spurious','空白误读出日期'], ['date_with_value','有日期样单'], ['date_blank','空白日期样单']]),
      ['wrong','判定错误'], ['unrecognized','未识别'], ['failed','执行失败'], ['missing', '未识别 / 失败'], ['pending_truth','待标注'], ['untested','未测试']];
    const overviewMarkup = section === 'overview' ? statisticsOverview(scoped, {tab, columns, groupBy:tab === 'date' ? 'company' : groupBy, showAllGroups, distribution:statisticsDistribution}) : '';
    const detailsMarkup = `<div class="sample-stat-detail-toolbar"><div class="sample-stat-detail-scope">${tab === 'seal' ? `<label>识别方案 <select data-stat-channel-select><option value="">全部方案</option>${columns.map(column => `<option value="${esc(column.key)}"${channel === column.key ? ' selected' : ''}>${esc(column.label)}</option>`).join('')}</select></label>` : ''}${requirement ? `<span>签章要求：${esc(requirement)}</span>` : ''}${channel || requirement || filter ? '<button type="button" class="text-button" data-stat-clear>清除明细筛选</button>' : ''}</div><span>共 ${rows.length} 张</span></div>
      <div class="sample-stat-filters" role="group" aria-label="筛选统计结果">${filters.map(([key, label]) =>
        `<button type="button" data-stat-filter="${key}" class="secondary-button${filter === key ? ' active' : ''}" aria-pressed="${filter === key}">${label}</button>`).join('')}</div>
      <div class="sample-stat-table-wrap"><table class="sample-stat-table" style="min-width:${Math.max(850, (columns.length + 3) * 170)}px">
      <thead><tr><th>样单 / 客户公司</th><th>${tab === 'seal' ? '签章要求 / 标注结论' : '标注日期'}</th>
      ${columns.map(column => `<th>${esc(column.label)}</th>`).join('')}<th>操作</th></tr></thead>
      <tbody>${rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map(row =>
        `<tr><td><div class="sample-stat-sample">${imageUrl ? `<button type="button" class="sample-stat-thumb" data-stat-view="${esc(row.filename)}" aria-label="查看 ${esc(row.filename)}"><img src="${esc(imageUrl(row.filename, true))}" alt="" loading="lazy"></button>` : ''}<div><strong>${esc(row.filename)}</strong><small>${esc(row.company)}</small><small>${row.job_status ? esc(statTime(row.tested_at)) : '未测试'}</small>${row.task_name ? `<small>${esc(row.task_name)}</small>` : ''}</div></div></td>
        <td>${esc(tab === 'seal' ? row.requirement || '未标注签章要求' : row.expected_date === null ? '待标注' : row.expected_date || '空白')}
        ${tab === 'seal' ? `<small>标注：${esc(row.expected_seal ? `应${row.expected_seal}` : '待标注')}</small>` : ''}</td>
        ${columns.map(column => `<td${channel === column.key ? ' class="sample-stat-focused-channel"' : ''}>${statisticsCell(tab === 'date' ? row.date : sealCell(row, column.key), tab === 'seal')}</td>`).join('')}
        <td><button type="button" class="text-button" data-stat-view="${esc(row.filename)}">${row.result_id ? '查看' : '预览'} ›</button></td></tr>`).join('')
        || `<tr><td colspan="${columns.length + 3}" class="empty-state">当前范围没有符合条件的样单</td></tr>`}</tbody></table></div>`;
    const markup = section === 'overview' ? overviewMarkup : detailsMarkup;
    if (markup !== rendered) {
      // Preserve keyboard focus during automatic status refresh.
      const focusedFilter = environment.document.activeElement?.dataset?.statFilter;
      const focusedSample = environment.document.activeElement?.dataset?.statView;
      const table = $('#sample-statistics-body').querySelector?.('.sample-stat-table-wrap');
      const tableTop = resetTableScroll ? 0 : table?.scrollTop || 0, tableLeft = table?.scrollLeft || 0;
      const bodyTop = resetTableScroll ? 0 : $('#sample-statistics-body').scrollTop || 0;
      $('#sample-statistics-body').innerHTML = markup;
      $$('[data-stat-analysis]', $('#sample-statistics-body')).forEach(panel => {
        panel.open = expandedAnalysis.get(`${tab}:${panel.dataset.statAnalysis}`) || false;
      });
      const updatedTable = $('#sample-statistics-body').querySelector?.('.sample-stat-table-wrap');
      if (updatedTable) { updatedTable.scrollTop = tableTop; updatedTable.scrollLeft = tableLeft; }
      rendered = markup;
      $('#sample-statistics-body').scrollTop = bodyTop;
      if (focusedFilter !== undefined) $$('[data-stat-filter]').find(button => button.dataset.statFilter === focusedFilter)?.focus();
      if (focusedSample !== undefined) $$('[data-stat-view]').find(button => button.dataset.statView === focusedSample)?.focus({preventScroll:true});
    }
    $('#sample-statistics-note').textContent = tab === 'seal'
      ? '按人工标注的“应匹配 / 应不匹配”评估各路判定，不代表印章逐字转写准确率。未识别、失败计入分母；未测试、未运行、测试中、待标注不计入。'
      : '识别日期与人工标注日期比较，标注为空白且未读出日期也算正确。未识别、失败计入分母；未测试、未运行、测试中、待标注不计入。';
    $('#sample-statistics-page').textContent = `显示 ${rows.length ? (page - 1) * PAGE_SIZE + 1 : 0}–${Math.min(page * PAGE_SIZE, rows.length)}，共 ${rows.length} 张 · ${page} / ${pages}`;
    $('#sample-statistics-prev').disabled = page <= 1;
    $('#sample-statistics-next').disabled = page >= pages;
  }

  async function refresh({reset = false} = {}) {
    if (preview.active()) { refreshDeferred = true; return; }
    if (!dialog().open || (loading && !reset)) return;
    const token = ++revision;
    loading = true;
    syncExport();
    if (reset) {
      data = null; rendered = '';
      $('#sample-statistics-body').innerHTML = '<p class="empty-state">正在加载统计结果…</p>';
      $('#sample-statistics-summary').innerHTML = '';
      $('#sample-statistics-scope-note').textContent = '正在读取所选范围的测试记录…';
      $('#sample-statistics-scope-summary').textContent = '正在读取统计范围…';
      render();
    }
    $('#sample-statistics-feedback').textContent = '';
    try {
      const result = await services.samples.statistics(taskId);
      if (token !== revision || !dialog().open) return;
      if (preview.active()) { refreshDeferred = true; return; }
      data = result;
      if (channel && !data.columns.some(column => column.key === channel)) channel = '';
      const select = $('#sample-statistics-batch');
      const batchOptions = batches => batches.map(batch => {
        const name = (batch.name || '样单测试').replace(/\s*·\s*\d+\s*张$/, '');
        return `<option value="${esc(batch.id)}">${esc(statTime(batch.created_at))} · ${esc(name)} · ${batch.total} 张 · ${esc(batch.status || '状态未记录')}</option>`;
      }).join('');
      const many = data.batches.filter(batch => batch.total > 1), single = data.batches.filter(batch => batch.total <= 1);
      const options = '<option value="">请选择测试批次</option>'
        + (many.length ? `<optgroup label="多张样单测试">${batchOptions(many)}</optgroup>` : '')
        + (single.length ? `<optgroup label="单张样单测试">${batchOptions(single)}</optgroup>` : '');
      if (select.innerHTML !== options) { select.innerHTML = options; select.value = taskId; }
      render(reset);
    } catch (error) {
      if (token !== revision || !dialog().open) return;
      $('#sample-statistics-feedback').textContent = `统计加载失败：${error.message}。请点击刷新重试。`;
      if (!data) $('#sample-statistics-body').innerHTML = '';
    } finally { if (token === revision) { loading = false; syncExport(); } }
  }

  async function exportExcel() {
    if (!data || loading || exporting) return;
    const params = {company, task_id: taskId, tab, filter: section === 'details' ? filter : '',
      channel:section === 'details' ? channel : '', requirement:section === 'details' ? requirement : ''};
    const title = tab === 'seal' ? '印章结果' : '日期结果';
    exporting = true; syncExport();
    $('#sample-statistics-export').textContent = '导出中…';
    try {
      const response = await environment.window.fetch(services.samples.statisticsExportUrl(params));
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || '导出失败，请重试');
      }
      const url = environment.window.URL.createObjectURL(await response.blob());
      const link = environment.document.createElement('a');
      link.href = url;
      link.download = `样单统计-${params.company || '全部公司'}-${title}.xlsx`;
      environment.document.body.appendChild(link);
      link.click(); link.remove();
      environment.window.setTimeout(() => environment.window.URL.revokeObjectURL(url), 60000);
      toast('Excel 已生成，包含当前筛选下的全部记录', 'success');
    } catch (error) { toast(error.message, 'danger'); }
    finally {
      exporting = false;
      $('#sample-statistics-export').textContent = '导出 Excel';
      syncExport();
    }
  }

  function show(context) {
    preview.clear(); refreshDeferred = false; expandedAnalysis.clear();
    $('#sample-statistics-explanation').open = false;
    company = context.company; knownCompanies = context.companies;
    page = 1; filter = ''; taskId = ''; section = 'overview'; channel = ''; requirement = '';
    groupBy = company ? 'requirement' : 'company'; showAllGroups = false;
    typeFilters.date = ''; typeFilters.seal = '';
    $('#sample-statistics-batch').value = '';
    $('#sample-statistics-scope-mode').value = 'latest';
    $('#sample-statistics-batch-label').hidden = true;
    $('#sample-statistics-company').innerHTML = '<option value="">全部公司</option>' +
      knownCompanies.map(row => `<option value="${esc(row.name)}">${esc(row.name)}</option>`).join('');
    $('#sample-statistics-company').value = company;
    if (!dialog().open) dialog().showModal();
    return refresh({reset: true});
  }

  function initialize() {
    preview.initialize();
    const explanation = $('#sample-statistics-explanation');
    dialog().addEventListener('click', event => {
      if (explanation.open && !explanation.contains(event.target)) explanation.open = false;
    });
    dialog().addEventListener('keydown', event => {
      if (event.key === 'Escape' && explanation.open) {
        event.preventDefault(); event.stopPropagation(); explanation.open = false;
      }
    });
    $('#sample-statistics-company').addEventListener('change', event => {
      company = event.target.value; requirement = ''; page = 1; showAllGroups = false;
      groupBy = company ? 'requirement' : 'company'; render(true);
    });
    $('#sample-statistics-scope-mode').addEventListener('change', event => {
      const batchMode = event.target.value === 'batch';
      if (batchMode && !data?.batches.length) {
        event.target.value = 'latest'; toast('暂无测试批次，请先运行样单测试', 'warning'); return;
      }
      $('#sample-statistics-batch-label').hidden = !batchMode;
      taskId = batchMode ? data.batches[0].id : '';
      $('#sample-statistics-batch').value = taskId;
      page = 1; showAllGroups = false; refresh({reset:true});
    });
    $('#sample-statistics-batch').addEventListener('change', event => {
      if (!event.target.value) return;
      taskId = event.target.value; page = 1; showAllGroups = false; refresh({reset: true});
    });
    $('#sample-statistics-refresh').addEventListener('click', () => refresh());
    $('#sample-statistics-export').addEventListener('click', exportExcel);
    $$('[data-stat-tab]').forEach(button => button.addEventListener('click', () => {
      typeFilters[tab] = filter;
      tab = button.dataset.statTab; filter = typeFilters[tab]; channel = ''; requirement = ''; page = 1; render(true);
    }));
    const showSection = next => { section = next; render(); };
    $$('[data-stat-section]').forEach(button => {
      button.addEventListener('click', () => showSection(button.dataset.statSection));
      button.addEventListener('keydown', event => {
        const buttons = $$('[data-stat-section]');
        const index = buttons.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
          : event.key === 'ArrowRight' ? (index + 1) % buttons.length
          : event.key === 'ArrowLeft' ? (index + buttons.length - 1) % buttons.length : -1;
        if (next < 0) return;
        event.preventDefault(); showSection(buttons[next].dataset.statSection); buttons[next].focus();
      });
    });
    $('#sample-statistics-show-details').addEventListener('click', () => {
      showSection('details'); $('#sample-statistics-details-tab').focus();
    });
    $('#sample-statistics-prev').addEventListener('click', () => { page--; render(true); });
    $('#sample-statistics-next').addEventListener('click', () => { page++; render(true); });
    async function handleStatisticsClick(event) {
      const drill = event.target.closest('[data-stat-drill]');
      if (drill) {
        filter = drill.dataset.statDrill; channel = drill.dataset.statChannel || ''; requirement = drill.dataset.statRequirement || '';
        if (drill.dataset.statCompany !== undefined) {
          company = drill.dataset.statCompany; $('#sample-statistics-company').value = company;
        }
        page = 1; section = 'details'; render(true); $('#sample-statistics-details-tab').focus(); return;
      }
      const grouping = event.target.closest('[data-stat-group-by]');
      if (grouping) { groupBy = grouping.dataset.statGroupBy; showAllGroups = false; render(); return; }
      if (event.target.closest('[data-stat-show-groups]')) { showAllGroups = !showAllGroups; render(); return; }
      if (event.target.closest('[data-stat-clear]')) { filter = ''; channel = ''; requirement = ''; page = 1; render(true); return; }
      const chip = event.target.closest('[data-stat-filter]');
      if (chip) { filter = chip.dataset.statFilter; if (['remote','local'].includes(filter)) channel = ''; page = 1; render(true); return; }
      const button = event.target.closest('[data-stat-view]');
      if (!button || !data) return;
      const row = data.items.find(item => item.filename === button.dataset.statView);
      if (!row) return;
      await preview.open(row, filterStatistics(data.items, {company, tab, filter, channel, requirement})
        .filter(item => item.result_id).map(item => item.result_id), button);
    }
    $('#sample-statistics-body').addEventListener('click', handleStatisticsClick);
    $('#sample-statistics-summary').addEventListener('click', handleStatisticsClick);
    $('#sample-statistics-body').addEventListener('change', event => {
      if (event.target.matches('[data-stat-channel-select]')) { channel = event.target.value; page = 1; render(true); }
    });
    dialog().addEventListener('close', () => {
      if (dialog().open) return;
      revision++; loading = false; refreshDeferred = false; preview.clear();
    });
  }
  return {initialize, show, refresh};
}
