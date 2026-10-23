import {escapeHtml} from '../core/ui.mjs';

export const DEFAULT_RESULT_FILTERS = Object.freeze({date: '', sealSource: 'combined', seal: '', signature: '',
  dateAccuracy: '', sealAccuracy: '', compareSource: '', compareSeal: ''});
const SOURCES = [
  {key:'combined',label:'综合结果'}, {key:'qingtong_template',label:'清瞳 · 印章模板'},
  {key:'qingtong_ocr',label:'清瞳 · 文字 OCR'}, {key:'local:paddle_v6',label:'本地 · PP-OCRv6'},
  {key:'local:paddle_seal',label:'本地 · 印章专用 OCR'}, {key:'danzhengtong',label:'单证通'},
  {key:'local:legacy_local',label:'本地 · 历史结果'},
];
const COMMON = [['recognized','已识别'],['未识别','未识别'],['需人工复核','需人工复核'],
  ['未运行','未运行'],['调用失败','调用失败'],['缺少结果','缺少结果'],['未测试','未测试'],['测试中','测试中']];
const DATE = [['匹配','匹配'],['不匹配','不匹配'],...COMMON];
const SEAL = [['匹配','满足要求'],['不匹配','不满足要求'],['部分匹配','部分满足'],...COMMON];
const ACCURACY = [['正确','正确'],['错误','错误'],['待标注','待标注'],['未评估','未评估']];
const SIGNATURE = [['匹配','匹配'],['不匹配','不匹配'],['未识别','未识别'],['需人工复核','需人工复核'],
  ['未执行','未执行'],['未测试','未测试'],['测试中','测试中'],['调用失败','调用失败']];
const FIELDS = ['date','sealSource','seal','signature','dateAccuracy','sealAccuracy','compareSource','compareSeal'];
const IDS = Object.fromEntries(FIELDS.map(key => [key, `#sample-filter-${key.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase())}`]));
const choices = {date:DATE, seal:SEAL, signature:SIGNATURE, dateAccuracy:ACCURACY, sealAccuracy:ACCURACY, compareSeal:SEAL};
const labels = {date:'日期',sealSource:'印章来源',seal:'印章',signature:'签名',dateAccuracy:'日期识别',
  sealAccuracy:'印章判定',compareSource:'对照来源',compareSeal:'对照结果'};

export function resultFilterCell(item, key, source = 'combined') {
  const cell = key === 'date' ? item.result_filters?.date : item.result_filters?.seals?.[source];
  if (cell) return cell;
  const state = item.status === '未测试' ? '未测试' : item.status === '测试中' ? '测试中'
    : item.status === '运行失败' ? '调用失败' : key === 'date' ? item.checks?.date
      : source === 'combined' ? item.checks?.seal : '';
  return {state:state || '缺少结果',value:'',recognized:false,accuracy:'未评估'};
}

export function matchesResultFilters(item, filters = {}) {
  if (!filters.date && !filters.seal && !filters.signature && !filters.dateAccuracy && !filters.sealAccuracy
      && !(filters.compareSource && filters.compareSeal)) return true;
  const f = {...DEFAULT_RESULT_FILTERS, ...filters};
  const date = resultFilterCell(item, 'date'), seal = resultFilterCell(item, 'seal', f.sealSource);
  const matches = (cell, value) => !value || (value === 'recognized' ? cell.recognized : cell.state === value);
  let signature = item.signature || '未执行';
  if (item.status === '未测试' || item.status === '测试中') signature = item.status;
  else if (item.status === '运行失败') signature = '调用失败';
  return matches(date,f.date) && matches(seal,f.seal)
    && (!f.signature || signature === f.signature)
    && (!f.dateAccuracy || date.accuracy === f.dateAccuracy)
    && (!f.sealAccuracy || seal.accuracy === f.sealAccuracy)
    && (!f.compareSource || matches(resultFilterCell(item,'seal',f.compareSource),f.compareSeal));
}

export function hasResultFilters(filters = {}) {
  return FIELDS.some(key => (filters[key] ?? DEFAULT_RESULT_FILTERS[key]) !== DEFAULT_RESULT_FILTERS[key]);
}

export function sampleFilterEvidence(item, filters = {}, sources = SOURCES) {
  const selected = [...new Set([filters.sealSource !== 'combined' && filters.sealSource, filters.compareSource].filter(Boolean))];
  return selected.map(source => {
    const cell = resultFilterCell(item,'seal',source);
    const label = sources.find(row => row.key === source)?.label || source;
    const state = new Map(SEAL).get(cell.state) || cell.state;
    const title = `${label}：${state}${cell.value ? `；返回：${cell.value}` : ''}`;
    return `<p class="sample-filter-evidence" data-match="${cell.state === '匹配' ? 'true' : 'false'}" title="${escapeHtml(title)}">${escapeHtml(label)}：${escapeHtml(state)}</p>`;
  }).join('');
}

export function createResultFilters({environment, ui, changed}) {
  const {$} = ui;
  let filters = {...DEFAULT_RESULT_FILTERS}, sources = SOURCES;
  let expanded = false, lastChips = '';
  const optionMarkup = (rows, first = '全部') => `<option value="">${first}</option>`
    + rows.map(([value,label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
  const sourceOptions = () => sources.map(row => `<option value="${escapeHtml(row.key)}">${escapeHtml(row.label)}</option>`).join('');
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
  function updateSources(rows) {
    if (!Array.isArray(rows) || !rows.length || JSON.stringify(rows) === JSON.stringify(sources)) return;
    sources = rows;
    $(IDS.sealSource).innerHTML = sourceOptions();
    $(IDS.compareSource).innerHTML = '<option value="">不对照</option>' + sourceOptions();
    syncControls();
  }
  function render(count, total, busy, scoped) {
    const chips = FIELDS.filter(key => filters[key] !== DEFAULT_RESULT_FILTERS[key]).map(key => {
      const value = key.endsWith('Source') ? sources.find(row => row.key === filters[key])?.label
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
    evidence:item => sampleFilterEvidence(item,filters,sources)};
}
