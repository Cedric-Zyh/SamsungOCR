import {escapeHtml} from '../core/ui.mjs';
import {receivingObservedComponents} from './receiving_components.mjs';
import {savedSealText, savedSealComponents, sealProcessingSteps} from './seal_component_fields.mjs';

const objects = value => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : [];
const compact = value => String(value || '').replace(/\s+/gu, '');
export const sealImageUrl = value => typeof value === 'string' && value.startsWith('/files/')
  && !/[\\\u0000-\u001f]/u.test(value) && !value.split(/[/?#]/u).includes('..') ? value : '';
const purposePattern = /^(.*?)(收[^①-⑳()（）\d０-９]{1,4}专用章)([①-⑳]|\([0-9０-９]{1,3}\)|（[0-9０-９]{1,3}）|[0-9０-９]{1,3})?$/u;

export function sealWorkspaceModel(item) {
  const form = item.receiving_form || {}, primary = form.primary || {};
  const adopted = form.managed === true && form.status === 'adopted' && typeof form.identity_text === 'string' && Boolean(form.identity_text);
  const selected = adopted ? form.identity_text : '';
  const parts = selected.match(purposePattern);
  const observed = receivingObservedComponents(item);
  const inputs = objects(item.inputs), rows = objects(primary.identity_rows);
  const purposeIndex = Number.isInteger(primary.purpose_row_index) ? primary.purpose_row_index : null;
  const titleIndex = [...rows, ...objects(primary.conflicts)].find(row => row.row_index !== purposeIndex)?.row_index
    ?? inputs.find(input => input.channel === 'receiving_primary' && input.rectangle_row_index !== purposeIndex
      && !objects(primary.form_rows).some(row => row.row_index === input.rectangle_row_index))?.rectangle_row_index;
  const original = sealImageUrl(item.original_url);
  const processed = sealImageUrl(item.color_isolated_oriented_url || item.color_isolated_url);
  const rowImage = index => {
    if (!Number.isInteger(index) || index < 0) return '';
    const channelOrder = form.selected_source === 'verified_grid_recovery'
      ? ['receiving_grid', 'receiving_primary', 'receiving_detection', 'body']
      : ['receiving_primary', 'receiving_detection', 'body', 'receiving_grid'];
    for (const channel of channelOrder) {
      const input = inputs.find(input => input.channel === channel
        && (input.rectangle_row_index ?? input.row_index) === index
        && (channel !== 'receiving_grid' || (input.receiving_direction ?? input.direction) === 0)
        && sealImageUrl(input.image_url));
      if (input) return input.image_url;
    }
    return sealImageUrl(rows.find(row => row.row_index === index)?.image_url)
      || sealImageUrl(item.rectangular_row_urls?.[index]);
  };
  const numberImage = objects(item.receiving_number?.inputs).find(input => sealImageUrl(input.image_url))?.image_url;
  const titleConflicts = !adopted ? objects(primary.conflicts).filter(row => row.row_index === titleIndex)
    .flatMap(row => [...objects(row.observations), ...objects(row.pairs).flatMap(pair => [pair.a, pair.b])])
    .map(row => row?.text).filter(Boolean) : [];
  let fields = form.managed ? [
    {label: '仓库与代码', value: parts?.[1]?.trim() || '', url: rowImage(titleIndex),
      conflict: [...new Set(titleConflicts)].join(' / ')},
    {label: '印章用途', value: parts?.[2] || '', url: rowImage(purposeIndex),
      conflict: observed?.purposeConflict ? observed.purposeTexts.join(' / ') : ''},
    {label: '印章编号', value: parts?.[3] || observed?.numbers?.join(' / ') || '',
      observed: !parts?.[3] && Boolean(observed?.numbers?.length),
      conflict: observed?.numberStatus === 'conflict' ? observed.numbers.join(' / ') : '',
      url: sealImageUrl(numberImage) || rowImage(purposeIndex)},
  ] : [];
  let formFields = objects(form.field_rows?.length ? form.field_rows : primary.form_rows).map(row => {
    const values = [row.text, ...objects(row.variants).map(value => value.text)].filter(Boolean);
    const label = values.some(value => /^\s*(?:收货人|接收人|签收人)/u.test(value)) ? '收货人'
      : values.some(value => /^\s*日期/u.test(value)) ? '日期' : `填写栏 ${Number(row.row_index) + 1}`;
    return {label, value: row.text || '未读到文字', url: sealImageUrl(row.image_url) || rowImage(row.row_index)};
  });
  const candidate = compact(form.candidate_text), identity = compact(selected);
  // An older reading missing only an already adopted number is audit data, not a new conflict.
  const omittedNumber = parts?.[3] && candidate === compact(parts[1] + parts[2]);
  const otherCandidate = candidate && candidate !== identity && !omittedNumber ? form.candidate_text : '';
  if (!form.managed) ({fields, formFields} = savedSealComponents(item));
  const resultText = selected || (form.managed ? '' : savedSealText(item));
  return {adopted, managed: form.managed === true, selected,
    text: resultText,
    emptyText: form.managed ? '尚无可采用的完整结果' : fields.length ? '已保存分项读数，请查看下方内容' : '这条记录未保存识别文字',
    status: adopted ? '已采用' : form.managed ? '待核对' : resultText ? '识别文字' : fields.length ? '分项结果' : '无文字记录',
    original, processed, fields, formFields, otherCandidate,
    warning: form.managed && !adopted ? (form.status === 'conflict'
      ? '识别结果存在分歧，请查看对应图片与识别过程。' : '尚未形成完整的印章文字，请查看原图。') : '',
  };
}

export function renderSealWorkspace(item, {audit = ''} = {}) {
  const model = sealWorkspaceModel(item);
  const button = (label, url, text = '查看依据') => sealImageUrl(url)
    ? `<button type="button" class="text-button" data-seal-evidence-url="${escapeHtml(url)}" data-seal-evidence-label="${escapeHtml(label)}" aria-label="查看${escapeHtml(label)}的图片依据">${text}<span aria-hidden="true"> ›</span></button>`
    : '<small class="seal-evidence-missing">未保存图片</small>';
  const row = field => `<div class="seal-result-field${field.conflict ? ' has-conflict' : ''}"><span>${escapeHtml(field.label)}</span><div><strong>${escapeHtml(field.conflict || field.value || '未识别')}</strong>${field.conflict ? '<small>读数有分歧</small>' : field.observed ? '<small>已读出，尚未并入结果</small>' : field.note ? `<small>${escapeHtml(field.note)}</small>` : ''}</div>${button(field.label, field.url)}</div>`;
  const formFields = model.formFields.length ? `<details class="seal-filled-fields"><summary>收货人、日期<span>填写内容</span></summary><p>填写内容不参与印章身份匹配，下方保留实际读数。</p>${model.formFields.map(row).join('')}</details>` : '';
  const steps = sealProcessingSteps(item).filter(step => sealImageUrl(step.url));
  const process = `<details class="seal-recognition-process"><summary>识别过程<span>处理图片与全部读数</span></summary>
    ${steps.length ? `<div class="seal-processing-steps">${steps.map((step, i) => `<div><span>${i + 1}</span><strong>${escapeHtml(step.label)}</strong>${button(step.label, step.url, '查看图片')}</div>`).join('')}</div>` : '<p>这条记录未保存独立的处理步骤图片。</p>'}
    <details class="seal-raw-record"><summary>完整识别记录<span>原始读数、候选与处理说明</span></summary>${audit}</details></details>`;
  return `<section class="seal-workspace"><header class="seal-result-heading"><h4>印章识别结果</h4><span class="seal-adoption${model.adopted ? ' is-adopted' : ''}">${model.status}</span></header>
    <div class="seal-final-text">${escapeHtml(model.text || model.emptyText)}</div>
    ${model.warning ? `<p class="seal-result-warning">${escapeHtml(model.warning)}</p>` : ''}
    <div class="seal-image-entry">${button('印章原图', model.original || model.processed, '查看印章图片')}<small>在左侧查看原图与处理图</small></div>
    ${model.fields.length ? `<div class="seal-result-fields">${model.fields.map(row).join('')}</div>` : ''}
    ${model.otherCandidate ? `<details class="seal-other-candidate"><summary>${model.adopted ? '其他识别读数' : '待核对候选'}</summary><p>${escapeHtml(model.otherCandidate)}</p></details>` : ''}
    ${formFields}${process}</section>`;
}

export function bindSealWorkspaces(root, items, previewSeal) {
  if (!root?.querySelectorAll || !items.length) return;
  const panels = [...root.querySelectorAll('[data-seal-workspace-panel]')];
  if (!panels.length) return;
  let selected = 0;
  const selection = () => ({label: `印章 ${selected + 1}`, ...sealWorkspaceModel(items[selected])});
  const show = (url, label, evidenceSelected = false) => previewSeal?.({...selection(), url, evidenceLabel: label, evidenceSelected});
  const activate = (index, preview) => {
    selected = index;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    root.querySelectorAll('[data-seal-workspace-select]').forEach(button => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.sealWorkspaceSelect) === index));
    });
    if (preview) show(selection().original || selection().processed, '印章原图');
  };
  root.querySelectorAll('[data-seal-workspace-select]').forEach(button => {
    button.addEventListener('click', () => activate(Number(button.dataset.sealWorkspaceSelect), true));
  });
  root.querySelectorAll('[data-seal-evidence-url]').forEach(button => {
    button.addEventListener('click', () => {
      root.querySelectorAll('[data-seal-evidence-url]').forEach(node => node.setAttribute('aria-pressed', String(node === button)));
      show(button.dataset.sealEvidenceUrl, button.dataset.sealEvidenceLabel, true);
    });
  });
  activate(0, false);
  return () => show(selection().original || selection().processed, '印章原图');
}
