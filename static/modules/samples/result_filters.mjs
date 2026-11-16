import {escapeHtml} from '../core/ui.mjs';

export const DEFAULT_RESULT_FILTERS = Object.freeze({dateSource: 'combined', date: '', sealSource: 'combined', seal: '',
  signatureSource: 'combined', signature: '',
  dateAccuracy: '', sealAccuracy: '', compareSource: '', compareSeal: ''});
const SOURCES = [
  {key:'combined',label:'综合结果'}, {key:'qingtong_template',label:'清瞳 · 印章模板'},
  {key:'qingtong_ocr',label:'清瞳 · 文字 OCR'}, {key:'local:paddle_v6',label:'本地 · PP-OCRv6'},
  {key:'local:paddle_seal',label:'本地 · 印章专用 OCR'}, {key:'danzhengtong',label:'单证通'},
  {key:'local:legacy_local',label:'本地 · 历史结果'},
];
const RECOGNITION_SOURCES = [
  {key:'combined',label:'综合结果'}, {key:'local:paddle_v6',label:'本地 · PP-OCRv6'},
  {key:'danzhengtong',label:'单证通'}, {key:'local:legacy_local',label:'本地 · 历史结果'},
];
const COMMON = [['recognized','已识别'],['未识别','未识别'],['需人工复核','需人工复核'],
  ['未运行','未运行'],['调用失败','调用失败'],['缺少结果','缺少结果'],['未测试','未测试'],['测试中','测试中']];
const DATE = [['匹配','匹配'],['不匹配','不匹配'],...COMMON];
const SEAL = [['匹配','匹配'],['不匹配','不匹配'],['部分匹配','部分匹配']];
const ACCURACY = [['正确','正确'],['错误','错误'],['待标注','待标注'],['未评估','未评估']];
const SIGNATURE = [['匹配','匹配'],['不匹配','不匹配'],...COMMON,['未执行','未执行']];
const FIELDS = ['dateSource','date','sealSource','seal','signatureSource','signature',
  'dateAccuracy','sealAccuracy','compareSource','compareSeal'];
const IDS = Object.fromEntries(FIELDS.map(key => [key, `#sample-filter-${key.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase())}`]));
const choices = {date:DATE, seal:SEAL, signature:SIGNATURE, dateAccuracy:ACCURACY, sealAccuracy:ACCURACY, compareSeal:SEAL};
const labels = {dateSource:'日期来源',date:'日期',sealSource:'印章来源',seal:'印章',
  signatureSource:'签名来源',signature:'签名',dateAccuracy:'日期识别',
  sealAccuracy:'印章判定',compareSource:'对照来源',compareSeal:'对照结果'};

export function resultFilterCell(item, key, source = 'combined') {
  const cell = key === 'date' ? item.result_filters?.dates?.[source]
    || (source === 'combined' && item.result_filters?.date)
    : key === 'signature' ? item.result_filters?.signatures?.[source] : item.result_filters?.seals?.[source];
  if (cell) return cell;
  const state = item.status === '未测试' ? '未测试' : item.status === '测试中' ? '测试中'
    : item.status === '运行失败' ? '调用失败' : source !== 'combined' ? ''
      : key === 'date' ? item.checks?.date : key === 'signature' ? item.signature || '未执行' : item.checks?.seal;
  return {state:state || '缺少结果',value:'',recognized:false,accuracy:'未评估'};
}

export function matchesResultFilters(item, filters = {}) {
  if (!filters.date && !filters.seal && !filters.signature && !filters.dateAccuracy && !filters.sealAccuracy
      && !(filters.compareSource && filters.compareSeal)) return true;
  const f = {...DEFAULT_RESULT_FILTERS, ...filters};
  const date = resultFilterCell(item, 'date', f.dateSource), seal = resultFilterCell(item, 'seal', f.sealSource);
  const signature = resultFilterCell(item, 'signature', f.signatureSource);
  const matches = (cell, value) => !value || (value === 'recognized' ? cell.recognized : cell.state === value);
  return matches(date,f.date) && matches(seal,f.seal)
    && matches(signature,f.signature)
    && (!f.dateAccuracy || date.accuracy === f.dateAccuracy)
    && (!f.sealAccuracy || seal.accuracy === f.sealAccuracy)
    && (!f.compareSource || matches(resultFilterCell(item,'seal',f.compareSource),f.compareSeal));
}

export function hasResultFilters(filters = {}) {
  return FIELDS.some(key => (filters[key] ?? DEFAULT_RESULT_FILTERS[key]) !== DEFAULT_RESULT_FILTERS[key]);
}

export function sampleFilterEvidence(item, filters = {}, sources = SOURCES,
  dateSources = RECOGNITION_SOURCES, signatureSources = RECOGNITION_SOURCES) {
  const selected = [
    {kind:'date',source:filters.dateSource,rows:dateSources,prefix:'日期 · '},
    ...[...new Set([filters.sealSource,filters.compareSource])].map(source => ({kind:'seal',source,rows:sources,prefix:''})),
    {kind:'signature',source:filters.signatureSource,rows:signatureSources,prefix:'签名 · '},
  ].filter(row => row.source && row.source !== 'combined');
  return selected.map(({kind,source,rows,prefix}) => {
    const cell = resultFilterCell(item,kind,source);
    const label = prefix + (rows.find(row => row.key === source)?.label || source);
    const state = new Map(SEAL).get(cell.state) || cell.state;
    const title = `${label}：${state}${cell.value ? `；返回：${cell.value}` : ''}`;
    return `<p class="sample-filter-evidence" data-match="${cell.state === '匹配' ? 'true' : 'false'}" title="${escapeHtml(title)}">${escapeHtml(label)}：${escapeHtml(state)}</p>`;
  }).join('');
}

export function createResultFilters({environment, ui, changed}) {
  const {$} = ui;
  let filters = {...DEFAULT_RESULT_FILTERS}, sources = SOURCES,
    dateSources = RECOGNITION_SOURCES, signatureSources = RECOGNITION_SOURCES;
  let expanded = false, lastChips = '';
  const optionMarkup = (rows, first = '全部') => `<option value="">${first}</option>`
    + rows.map(([value,label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
  const sourceOptions = (rows = sources) => rows.map(row => `<option value="${escapeHtml(row.key)}">${escapeHtml(row.label)}</option>`).join('');
  function syncControls() {
    for (const key of FIELDS) $(IDS[key]).value = filters[key];
    $(IDS.compareSeal).disabled = !filters.compareSource;
  }
  function change() {
    syncControls();
    changed();
  }
  function setExpanded(value) {
    expanded = value;
    $('#sample-result-filters-body').hidden = !expanded;
    $('#sample-result-filters-toggle').setAttribute('aria-expanded',String(expanded));
    $('#sample-result-filters-toggle').title = expanded ? '收起结果筛选' : '展开结果筛选';
    $('#sample-result-filters-toggle-label').textContent = '筛选';
  }
  function reset() { filters = {...DEFAULT_RESULT_FILTERS}; change(); }
  function initialize() {
    for (const [key,rows] of Object.entries(choices)) $(IDS[key]).innerHTML = optionMarkup(rows);
    $(IDS.sealSource).innerHTML = sourceOptions();
    $(IDS.dateSource).innerHTML = sourceOptions(dateSources);
    $(IDS.signatureSource).innerHTML = sourceOptions(signatureSources);
    $(IDS.compareSource).innerHTML = '<option value="">不对照</option>' + sourceOptions();
    syncControls();
    for (const key of FIELDS) $(IDS[key]).addEventListener('change', () => {
      filters[key] = $(IDS[key]).value;
      if (key === 'compareSource' && !filters.compareSource) filters.compareSeal = '';
      change();
    });
    $('#sample-result-filters-toggle').addEventListener('click', () => {
      setExpanded(!expanded);
      if (expanded) $(IDS.date).focus();
    });
    $('#sample-result-filters-close').addEventListener('click', () => {
      setExpanded(false); $('#sample-result-filters-toggle').focus();
    });
    environment.document.addEventListener?.('click', event => {
      if (expanded && !$('#sample-result-filters-body').contains(event.target)
          && !$('#sample-result-filters-toggle').contains(event.target)) setExpanded(false);
    });
    environment.document.addEventListener?.('keydown', event => {
      if (expanded && event.key === 'Escape') {
        event.preventDefault(); setExpanded(false); $('#sample-result-filters-toggle').focus();
      }
    });
    environment.window.addEventListener('hashchange', () => setExpanded(false));
    $('#sample-result-filters-clear').addEventListener('click', reset);
    $('#sample-result-filter-chips').addEventListener('click', event => {
      const button = event.target.closest('[data-clear-result-filter]');
      const key = button?.dataset.clearResultFilter;
      if (!FIELDS.includes(key)) return;
      filters[key] = DEFAULT_RESULT_FILTERS[key];
      if (key === 'compareSource') filters.compareSeal = '';
      change();
    });
    setExpanded(false);
  }
  function updateSources(rows, dates, signatures) {
    if (Array.isArray(rows) && rows.length && JSON.stringify(rows) !== JSON.stringify(sources)) {
      sources = rows;
      $(IDS.sealSource).innerHTML = sourceOptions();
      $(IDS.compareSource).innerHTML = '<option value="">不对照</option>' + sourceOptions();
    }
    if (Array.isArray(dates) && dates.length && JSON.stringify(dates) !== JSON.stringify(dateSources)) {
      dateSources = dates;
      $(IDS.dateSource).innerHTML = sourceOptions(dateSources);
    }
    if (Array.isArray(signatures) && signatures.length && JSON.stringify(signatures) !== JSON.stringify(signatureSources)) {
      signatureSources = signatures;
      $(IDS.signatureSource).innerHTML = sourceOptions(signatureSources);
    }
    syncControls();
  }
  function render(count, total, busy, scoped) {
    const chips = FIELDS.filter(key => filters[key] !== DEFAULT_RESULT_FILTERS[key]).map(key => {
      const sourceRows = key === 'dateSource' ? dateSources : key === 'signatureSource' ? signatureSources : sources;
      const value = key.endsWith('Source') ? sourceRows.find(row => row.key === filters[key])?.label
        : new Map(choices[key]).get(filters[key]);
      const label = `${labels[key]}：${value || filters[key]}`;
      return `<button type="button" data-clear-result-filter="${key}" aria-label="移除${escapeHtml(label)}">${escapeHtml(label)}<span aria-hidden="true">×</span></button>`;
    }).join('');
    if (chips !== lastChips) { $('#sample-result-filter-chips').innerHTML = chips; lastChips = chips; }
    const active = hasResultFilters(filters);
    const activeCount = FIELDS.filter(key => filters[key] !== DEFAULT_RESULT_FILTERS[key]).length;
    $('#sample-result-filters-badge').hidden = !active;
    $('#sample-result-filters-badge').textContent = activeCount;
    $('#sample-active-filters').hidden = !active && !scoped;
    $('#sample-result-filters-clear').hidden = !active;
    $('#sample-result-filters-count').textContent = `${active || scoped ? '符合条件' : '全部'} ${count} 张`;
    $('#sample-result-filters-count').title = `当前目录共 ${total} 张样单`;
    $('#sample-result-filters-run').hidden = !active && !scoped;
    $('#sample-result-filters-run').disabled = busy || !count;
  }
  return {initialize, updateSources, render, reset, current:() => filters,
    evidence:item => sampleFilterEvidence(item,filters,sources,dateSources,signatureSources)};
}
