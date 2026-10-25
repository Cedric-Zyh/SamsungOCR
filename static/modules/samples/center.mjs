import {escapeHtml} from '../core/ui.mjs';
import {createSampleStatistics} from './statistics.mjs';
import {createCompanyMarks, companyRowMarkup, requirementRowMarkup} from './company_marks.mjs';
import {createSampleLibrary} from './library.mjs';
import {createResultFilters, matchesResultFilters} from './result_filters.mjs';
import {createSampleThumbnails} from './thumbnails.mjs';

const PAGE_SIZE = 6;
const VIEW_STORAGE_KEY = 'sample-library-view-v1';
const actionIcons = {
  preview: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  result: '<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h5"/>',
  run: '<path d="M20 7v5h-5M20 12a8 8 0 1 1-2-5"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
};
const actionIcon = key => `<svg viewBox="0 0 24 24" aria-hidden="true">${actionIcons[key]}</svg>`;
export const sampleImageUrl = (name, thumbnail = false) =>
  `/api/samples/image/${encodeURIComponent(name)}${thumbnail ? '?thumbnail=1' : ''}`;

export function sampleScope(items, {company = '', search = '', status = '', requirement = null, resultFilters = {}} = {}) {
  const query = search.trim().toLocaleLowerCase();
  return items.filter(item => (!company || item.company === company)
    && (requirement === null || (item.requirement_key || '') === requirement)
    && (!query || item.filename.toLocaleLowerCase().includes(query)) && (!status || item.status === status)
    && matchesResultFilters(item, resultFilters));
}

export function sampleCompanyProgress(items) {
  const total = items.length;
  const completed = items.filter(item => item.status === '已完成').length;
  const running = items.filter(item => item.status === '测试中').length;
  const failed = items.filter(item => item.status === '运行失败').length;
  const state = total && completed === total ? 'complete' : running ? 'running' : failed ? 'failed' : completed ? 'partial' : 'untested';
  const label = state === 'complete' ? '✓ 已测完' : running ? `测试中 · 已完成 ${completed}/${total}`
    : failed ? `已完成 ${completed}/${total} · ${failed} 张失败` : completed ? `已完成 ${completed}/${total}` : '未测试';
  return {state, label, title: `共 ${total} 张，已完成 ${completed} 张，测试中 ${running} 张，运行失败 ${failed} 张。已测完表示测试执行完成，不代表识别结果全部正确。`};
}

export function sampleCard(item, selected, evidence = '') {
  const filename = escapeHtml(item.filename), busy = item.status === '测试中';
  const checks = [['日期', item.checks?.date], ['印章', item.checks?.seal], ['签名', item.signature]];
  const detail = item.status === '已完成'
    ? `<p class="sample-card-checks">${checks.map(([label, value]) => `<span data-match="${value === '匹配' ? 'true' : value === '未执行' || (label === '签名' && value === '未识别') ? '' : 'false'}">${label} ${escapeHtml(value || '未执行')}</span>`).join('')}</p>`
    : `<p class="${item.error ? 'sample-card-error' : ''}"${item.error ? ` title="${escapeHtml(item.error)}"` : ''}>${escapeHtml(item.error || (busy ? item.job_status === 'queued' ? '等待测试…' : '正在识别…' : '尚无测试结果'))}</p>`;
  return `<article class="sample-card${selected ? ' is-selected' : ''}" data-filename="${filename}">
    <div class="sample-card-image"><button class="sample-thumbnail" type="button" data-sample-preview aria-label="预览 ${filename}"><img data-sample-src="${sampleImageUrl(item.filename, true)}" loading="lazy" alt="${filename} 样单缩略图"></button><label><input type="checkbox" data-sample-select aria-label="选择 ${filename}" ${selected ? 'checked' : ''} ${busy ? 'disabled' : ''}></label><span class="sample-state" data-status="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></div>
    <div class="sample-card-copy"><h3>${filename}</h3>${detail}${evidence}
    <div class="sample-card-actions"><button type="button" data-sample-preview>${actionIcon('preview')}<span>预览</span></button><button type="button" data-sample-result ${item.result_id ? '' : 'disabled'}>${actionIcon('result')}<span>测试结果</span></button><button type="button" data-sample-run ${busy ? 'disabled' : ''}>${actionIcon('run')}<span>${busy ? '测试中…' : item.result_id || item.status === '运行失败' ? '重新测试' : '运行测试'}</span></button></div></div></article>`;
}

export function createSampleCenter({environment, ui, services, flushSettings, openResult, openOriginal}) {
  const {$, $$, toast} = ui;
  let items = [], companies = [], company = '', status = '', page = 1;
  let requirement = null, requirementGroups = {}, expanded = new Set();
  let classificationRequirement = null;
  let selected = new Set(), loadPromise = null, running = false, loaded = false, lastRender = '';
  let historyLoading = false, historyMarkup = '', historyRows = [];
  let view = 'grid';
  let companyMarkup = '';
  let lastAttemptAt = null, loadFailed = false;
  let thumbnailsCleared = false;
  const now = () => (environment.Date || Date).now();
  const thumbnails = createSampleThumbnails({environment});
  const resultFilters = createResultFilters({environment, ui, changed: () => {
    page = 1; selected.clear(); render({deferImages: true}); $('#sample-grid').scrollTop = 0;
  }});
  const companyMarks = createCompanyMarks({environment, ui, services, render: renderCompanies});
  const statistics = createSampleStatistics({environment, ui, services, openResult, openOriginal, imageUrl: sampleImageUrl});
  const library = createSampleLibrary({environment, ui, services,
    current: () => ({company, companies, selected, busy: running}),
    setBusy: busy => { running = busy; syncSelection(); },
    refresh: async importedCompany => {
      selected.clear();
      await load(true);
      if (importedCompany) {
        company = importedCompany; requirement = null; status = ''; page = 1;
        classificationRequirement = null;
        if (company !== '未分类') expanded.add(company);
        $('#sample-search').value = '';
        $('#sample-company-search').value = '';
        $('#sample-company-mark-filter [data-company-mark-filter=""]').click();
        resultFilters.reset();
      }
      renderCompanies(); render();
    },
  });
  const scope = () => sampleScope(items, {company, requirement, search: $('#sample-search').value, status,
    resultFilters: resultFilters.current()});
  const companyItems = () => sampleScope(items, {company});
  const groupItems = () => sampleScope(items, {company, requirement});
  const visible = () => scope().slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const browseIds = () => scope().filter(item => item.result_id).map(item => item.result_id);

  function renderCompanies() {
    const query = $('#sample-company-search').value.trim().toLocaleLowerCase();
    const rows = companies.filter(row => row.name !== '未分类' && row.name.toLocaleLowerCase().includes(query) && companyMarks.matches(row.name));
    const unclassified = {name: '未分类', count: items.filter(item => item.company === '未分类').length};
    const markup = [{name: '', count: items.length}, unclassified, ...rows].map((row, index) => {
      const progress = sampleCompanyProgress(sampleScope(items, {company: row.name}));
      const groups = requirementGroups[row.name] || [];
      const parent = companyRowMarkup(row, progress, company === row.name,
        {matched: groups.filter(group => group.key && group.has_standard).length}, expanded.has(row.name));
      const heading = index === 1 ? '<div class="sample-company-directory-heading"><span>公司目录</span><span>绿点 · 已测完</span></div>' : '';
      if (!row.name || row.name === '未分类' || !expanded.has(row.name)) return parent + heading;
      const ungrouped = groups.find(group => !group.key) || {key: '', name: '未归类', count: 0};
      const children = [ungrouped, ...groups.filter(group => group.key)];
      return parent + `<div class="sample-requirement-groups" aria-label="${escapeHtml(row.name)}的签章要求">${children.map(group =>
        requirementRowMarkup(row.name, group, company === row.name && requirement === group.key,
            sampleCompanyProgress(sampleScope(items, {company: row.name, requirement: group.key}))))
        .join('')}</div>`;
    }).join('') + (!rows.length ? '<p class="empty-state">没有符合条件的公司</p>' : '');
    if (markup !== companyMarkup) {
      const list = $('#sample-company-list'), scrollTop = list.scrollTop;
      const focused = environment.document.activeElement;
      const focusedCompany = list.contains(focused) ? focused.dataset.company : undefined;
      const focusedRequirement = focused?.dataset?.requirement;
      list.innerHTML = markup; companyMarkup = markup;
      if (focusedCompany !== undefined) $$('#sample-company-list [data-company]').find(button => button.dataset.company === focusedCompany
        && button.dataset.requirement === focusedRequirement)?.focus({preventScroll: true});
      list.scrollTop = scrollTop;
    }
    const shown = rows.length + (unclassified.count ? 1 : 0);
    $('#sample-company-count').textContent = `${shown === companies.length ? companies.length : `${shown} / ${companies.length}`} 家`;
    companyMarks.renderHeader(company);
  }

  function syncSelection() {
    const available = visible().filter(item => item.status !== '测试中');
    $('#sample-select-page').checked = available.length > 0 && available.every(item => selected.has(item.filename));
    $('#sample-select-page').indeterminate = available.some(item => selected.has(item.filename)) && !$('#sample-select-page').checked;
    $('#sample-select-page').disabled = !available.length || running;
    $('#sample-run-selected').disabled = running || !selected.size;
    $('#sample-delete-selected').disabled = running || !selected.size;
    $('#sample-import-open').disabled = running;
    const classificationItems = selected.size ? items.filter(item => selected.has(item.filename))
      : sampleScope(items, {company, requirement: classificationRequirement});
    $('#sample-classify').disabled = running || !classificationItems.some(item => item.status === '已完成' && item.result_id);
    const classificationLabel = selected.size ? '所选' : !company ? '全部'
      : classificationRequirement === null ? '本公司全部' : '当前签章要求下的';
    $('#sample-classify').title = `按最新结果归类${classificationLabel}已完成样单，可更正已有分组`;
    $('#sample-clear-selection').textContent = `已选 ${selected.size} 张${selected.size ? ' · 清空' : ''}`;
    $('#sample-clear-selection').disabled = !selected.size;
    $('#sample-batch-actions').hidden = !selected.size;
    $('#sample-run-company').disabled = running || !companyItems().some(item => item.status !== '测试中');
    $('#sample-result-filters-run').disabled = running || !scope().some(item => item.status !== '测试中');
    $$('#sample-grid [data-sample-select]').forEach(input => {
      input.checked = selected.has(input.closest('[data-filename]').dataset.filename);
      input.closest('.sample-card').classList.toggle('is-selected', input.checked);
    });
    $$('#sample-grid [data-sample-run]').forEach(button => {
      const item = items.find(row => row.filename === button.closest('[data-filename]').dataset.filename);
      button.disabled = running || item.status === '测试中';
    });
  }

  function render({deferImages = false} = {}) {
    const rows = scope(), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const names = new Set(rows.map(item => item.filename));
    selected = new Set([...selected].filter(name => names.has(name)));
    page = Math.min(page, pages);
    $('#sample-company-title').textContent = company || '全部样单';
    const groupLabel = requirement === null ? '' : requirementGroups[company]?.find(group => group.key === requirement)?.name || '未归类';
    $('#sample-library-summary').textContent = `${requirement !== null ? `当前分组 ${groupItems().length} 张 · ` : ''}${company ? `本公司 ${companyItems().length} 张` : `共 ${items.length} 张样单`} · ${sampleCompanyProgress(groupItems()).label}`;
    $('#sample-requirement-title').textContent = groupLabel || (company === '未分类' ? '未归类' : '本公司全部样单');
    $('#sample-requirement-title').title = $('#sample-requirement-title').textContent;
    $('#sample-run-company').innerHTML = `${actionIcon('play')}<span>${company ? '运行本公司全部' : '运行全部样单'}</span>`;
    $('#sample-classify').hidden = false;
    $('#sample-grid').dataset.view = view;
    $('#sample-grid').setAttribute('aria-label', view === 'grid' ? '样单卡片' : '样单列表');
    $$('[data-sample-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.sampleView === view)));
    const markup = visible().map(item => sampleCard(item, selected.has(item.filename), resultFilters.evidence(item))).join('') ||
      `<div class="empty-state">${items.length ? '当前条件下没有样单，请调整搜索或筛选。' : '暂无样单，点击右上角「导入样单」添加图片。'}</div>`;
    // Polling must not replace focused controls just because selection changed.
    const renderKey = JSON.stringify([visible(), resultFilters.current()]) + Boolean(items.length);
    if (renderKey !== lastRender) {
      $('#sample-grid').innerHTML = markup; lastRender = renderKey;
      thumbnails.replace($$('#sample-grid img[data-sample-src]'), {defer: deferImages});
      thumbnailsCleared = false;
    } else if (thumbnailsCleared) {
      // Returning to this page can restore images without replacing focused controls.
      thumbnails.replace($$('#sample-grid img[data-sample-src]'), {defer: deferImages});
      thumbnailsCleared = false;
    }
    $('#sample-page-info').textContent = rows.length ? `显示 ${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, rows.length)}，共 ${rows.length} 张` : '共 0 张';
    if (rows.length !== groupItems().length) $('#sample-page-info').textContent += `（筛选前 ${groupItems().length} 张）`;
    resultFilters.render(rows.length, groupItems().length, running, Boolean(status || $('#sample-search').value.trim()));
    $('#sample-page-number').textContent = `${page} / ${pages}`;
    $('#sample-prev').disabled = page <= 1;
    $('#sample-next').disabled = page >= pages;
    const statusRows = sampleScope(items, {company, requirement, search: $('#sample-search').value,
      resultFilters: resultFilters.current()});
    $$('[data-sample-status]').forEach(button => {
      const active = button.dataset.sampleStatus === status;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      const count = button.dataset.sampleStatus ? statusRows.filter(item => item.status === button.dataset.sampleStatus).length : statusRows.length;
      button.innerHTML = `${button.dataset.sampleStatus || '全部'} <span class="sample-status-count">${count}</span>`;
    });
    syncSelection();
  }

  function load(force = false) {
    if (loadPromise) return force ? loadPromise.then(() => load()) : loadPromise;
    loadPromise = loadOnce().finally(() => { loadPromise = null; });
    return loadPromise;
  }

  function poll() {
    const active = running || !loaded || loadFailed || items.some(item => item.status === '测试中');
    if (lastAttemptAt !== null && now() - lastAttemptAt < (active ? 2000 : 30000)) return loadPromise;
    return load();
  }

  async function loadOnce() {
    lastAttemptAt = now();
    try {
      const data = await services.samples.list();
      items = data.items; companies = data.companies;
      resultFilters.updateSources(data.filter_seal_sources);
      requirementGroups = data.requirement_groups || {};
      companyMarks.update(data);
      if (company && company !== '未分类' && !companies.some(row => row.name === company)) { company = ''; requirement = null; classificationRequirement = null; }
      if (requirement && !requirementGroups[company]?.some(group => group.key === requirement)) {
        requirement = ''; classificationRequirement = '';
      }
      selected = new Set([...selected].filter(name => items.some(item => item.filename === name && item.status !== '测试中')));
      renderCompanies();
      loaded = true;
      loadFailed = false;
      $('#sample-feedback').textContent = '';
      render();
      if ($('#sample-history-dialog').open) await refreshHistory();
      await statistics.refresh();
    } catch (error) {
      loadFailed = true;
      $('#sample-feedback').textContent = `加载失败：${error.message}，正在等待恢复连接。`;
      if (!loaded) $('#sample-grid').innerHTML = '<div class="empty-state">样单暂时无法加载</div>';
    }
  }

  async function classify() {
    if (running) return;
    running = true; syncSelection();
    $('#sample-classify').textContent = '归类中…';
    const targetCompany = company;
    const classificationScope = {requirement: selected.size ? null : classificationRequirement,
      ...(selected.size ? {filenames: [...selected]} : {})};
    try {
      const data = !targetCompany || targetCompany === '未分类'
        ? await services.samples.classify({company: targetCompany, ...classificationScope})
        : await services.samples.classifyRequirements(targetCompany, classificationScope);
      selected.clear();
      await load(true);
      toast(`已归类 ${data.classified.length} 张 · 检查 ${data.processed ?? data.classified.length} 张已完成样单 · 新建 ${data.created_companies?.length || 0} 家公司、${data.created_groups?.length || 0} 个签章要求${data.skipped ? ` · 跳过 ${data.skipped} 张未完成样单` : ''}`, 'success');
    } catch (error) { toast(error.message, 'danger'); }
    finally {
      running = false;
      $('#sample-classify').textContent = '自动归类';
      syncSelection();
    }
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
        delete item.result_filters;
        selected.delete(item.filename);
      }
      renderCompanies();
      render();
      toast(data.accepted.length ? `已开始 ${data.accepted.length} 张样单测试${data.skipped.length ? `，${data.skipped.length} 张已在测试中` : ''}` : '所选样单已在测试中', 'success');
      await load(true);
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
      historyRows = rows;
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
    try { if (environment.window.localStorage?.getItem(VIEW_STORAGE_KEY) === 'list') view = 'list'; } catch {}
    resultFilters.initialize();
    statistics.initialize();
    companyMarks.initialize();
    library.initialize();
    const renderFromTop = () => { render({deferImages: true}); $('#sample-grid').scrollTop = 0; };
    $$('[data-sample-view]').forEach(button => button.addEventListener('click', () => {
      view = button.dataset.sampleView;
      try { environment.window.localStorage?.setItem(VIEW_STORAGE_KEY, view); } catch {}
      renderFromTop();
    }));
    $('#sample-company-search').addEventListener('input', renderCompanies);
    $('#sample-company-list').addEventListener('click', event => {
      const button = event.target.closest('[data-company]');
      if (!button) return;
      const nextCompany = button.dataset.company;
      const isGroup = button.dataset.requirement !== undefined;
      if (isGroup) {
        requirement = button.dataset.requirement;
        classificationRequirement = requirement;
      } else if (nextCompany && nextCompany !== '未分类') {
        if (expanded.has(nextCompany)) expanded.delete(nextCompany);
        else expanded.add(nextCompany);
        requirement = null;
        classificationRequirement = null;
      } else { requirement = null; classificationRequirement = null; }
      company = nextCompany; page = 1; selected.clear();
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
        else if (event.target.closest('[data-sample-result]')) await openResult(item.result_id, {browseIds: browseIds()});
        else if (event.target.closest('[data-sample-preview]')) {
          if (item.result_id) await openResult(item.result_id, {preview: true, browseIds: browseIds()});
          else openOriginal({filename: item.filename, sample_center: true, preview_url: sampleImageUrl(item.filename)}, event.target.closest('button'));
        }
      } catch (error) { toast(error.message, 'danger'); }
    });
    $('#sample-run-selected').addEventListener('click', () => run([...selected]));
    $('#sample-result-filters-run').addEventListener('click', () => run(scope().filter(item => item.status !== '测试中').map(item => item.filename)));
    $('#sample-run-company').addEventListener('click', () => run(companyItems().map(item => item.filename)));
    $('#sample-classify').addEventListener('click', classify);
    $('#sample-history-open').addEventListener('click', showHistory);
    $('#sample-statistics-open').addEventListener('click', () => statistics.show({company, companies}));
    $('#sample-history-refresh').addEventListener('click', refreshHistory);
    $('#sample-history-body').addEventListener('click', async event => {
      const button = event.target.closest('[data-history-result]');
      if (!button?.dataset.historyResult) return;
      $('#sample-history-dialog').close();
      try {
        await openResult(Number(button.dataset.historyResult), {
          browseIds: historyRows.filter(row => row.result_id).map(row => row.result_id),
        });
      } catch (error) { toast(error.message, 'danger'); }
    });
    environment.window.addEventListener('hashchange', () => {
      thumbnails.clear(); thumbnailsCleared = true;
      $('#sample-history-dialog').close();
    });
  }
  return {initialize, load, poll, run};
}
