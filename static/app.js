const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {records: [], selected: new Set(), current: null, artifactTab: 'date', taskId: '', ocrBackend: ''};

const fileInput = $('#file-input');
const folderInput = $('#folder-input');
const dropZone = $('#drop-zone');
const ocrBackendSelect = $('#ocr-backend');
state.ocrBackend = ocrBackendSelect.value;
ocrBackendSelect.addEventListener('change', () => {
  state.ocrBackend = ocrBackendSelect.value;
  updateBackendDetail();
  Promise.all([loadRecords(), loadReport()]).catch(error => toast(error.message, 'danger'));
});
updateBackendDetail();
$('#choose-file').addEventListener('click', (event) => { event.stopPropagation(); fileInput.click(); });
$('#choose-folder').addEventListener('click', (event) => { event.stopPropagation(); folderInput.click(); });
dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => startBatch([...fileInput.files], [], '批量图片识别'));
folderInput.addEventListener('change', () => startBatch([...folderInput.files], [], `文件夹：${folderInput.files[0]?.webkitRelativePath?.split('/')[0] || '批量导入'}`));
['dragenter', 'dragover'].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove('dragging'); }));
dropZone.addEventListener('drop', event => startBatch([...event.dataTransfer.files], [], '拖拽批量识别'));

const sampleNames = $$('.sample-button[data-sample]').map(button => button.dataset.sample);
$$('.sample-button[data-sample]').forEach(button => button.addEventListener('click', () => startBatch([], [button.dataset.sample], `样单 ${button.dataset.sample}`)));
$('#run-all-samples').addEventListener('click', () => startBatch([], sampleNames, `现有 ${sampleNames.length} 张标注样单端到端测试`));

$('#filters').addEventListener('submit', event => { event.preventDefault(); loadRecords(); });
$('#reset-filters').addEventListener('click', () => setTimeout(loadRecords));
$('#refresh-report').addEventListener('click', loadReport);
$('#select-all').addEventListener('change', event => {
  $$('.record-select').forEach(input => { input.checked = event.target.checked; toggleSelected(Number(input.value), input.checked); });
});
$('#bulk-pass').addEventListener('click', () => bulkReview('确认通过', '通过'));
$('#bulk-pending').addEventListener('click', () => bulkReview('待复核', '需人工复核'));
$('#bulk-retry').addEventListener('click', bulkRetry);
$('#export-excel').addEventListener('click', () => {
  const params = new URLSearchParams(new FormData($('#filters')));
  params.set('ocr_backend', state.ocrBackend);
  window.location.href = `/api/export.xlsx?${params}`;
});

async function startBatch(files, samples, name) {
  files = files.filter(file => file.type.startsWith('image/') || /\.(jpg|jpeg|png|bmp|webp)$/i.test(file.name));
  const items = [...files.map(file => ({name: file.webkitRelativePath || file.name, file})), ...samples.map(sample => ({name: sample, sample}))];
  fileInput.value = ''; folderInput.value = '';
  if (!items.length) return toast('没有找到支持的图片', 'warning');
  const task = await api('/api/tasks', {method: 'POST', json: {total: items.length, name, ocr_backend: state.ocrBackend}});
  state.taskId = task.id;
  $('#task-section').classList.remove('hidden');
  $('#task-name').textContent = name;
  $('#queue').innerHTML = '';
  renderTask(task);
  const queueRows = items.map((item, index) => createQueueRow(item.name, index));
  await runPool(items.map((item, index) => () => processItem(item, queueRows[index], task.id)), 2);
  const finalTask = await api(`/api/tasks/${task.id}`);
  renderTask(finalTask);
  await Promise.all([loadRecords(), loadReport()]);
  toast(`批量任务完成：成功 ${finalTask.succeeded}，失败 ${finalTask.failed}，待复核 ${finalTask.pending_review}`, finalTask.failed ? 'warning' : 'success');
}

async function runPool(tasks, concurrency) {
  let cursor = 0;
  async function worker() { while (cursor < tasks.length) { const index = cursor++; await tasks[index](); } }
  await Promise.all(Array.from({length: Math.min(concurrency, tasks.length)}, worker));
}

function createQueueRow(name, index) {
  const row = document.createElement('div');
  row.className = 'queue-item'; row.dataset.index = index;
  row.innerHTML = `<span class="spinner"></span><div><strong>${escapeHtml(name)}</strong><small>等待本地 OCR…</small></div><b>排队中</b>`;
  $('#queue').append(row); return row;
}

async function processItem(item, row, taskId) {
  row.querySelector('b').textContent = '处理中'; row.querySelector('small').textContent = '字段、日期、印章与中间证据生成中…';
  const form = new FormData(); form.append('task_id', taskId);
  form.append('ocr_backend', state.ocrBackend);
  if (item.file) form.append('file', item.file); else form.append('sample', item.sample);
  try {
    const result = await api('/api/analyze', {method: 'POST', body: form});
    row.classList.add('done');
    row.innerHTML = `<span class="done-icon">✓</span><div><strong>${escapeHtml(item.name)}</strong><small>${result.processing_seconds} 秒 · ${escapeHtml(result.document_type?.label || '未分类')} · ${escapeHtml(result.overall)}</small></div><b>${escapeHtml(result.review_status)}</b>`;
  } catch (error) {
    row.classList.add('failed');
    row.innerHTML = `<span class="error-icon">!</span><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(error.message)}</small></div><b>失败</b>`;
  }
  renderTask(await api(`/api/tasks/${taskId}`));
}

function renderTask(task) {
  const percent = task.total ? Math.round(task.completed / task.total * 100) : 0;
  $('#task-summary').textContent = `${percent}% · ${task.status}${task.error_message ? ` · ${task.error_message}` : ''}`;
  $('#task-progress').style.width = `${percent}%`;
  $('#task-total').textContent = task.total; $('#task-completed').textContent = task.completed;
  $('#task-success').textContent = task.succeeded; $('#task-failed').textContent = task.failed;
  $('#task-review').textContent = task.pending_review;
}

async function loadRecords() {
  const params = new URLSearchParams(new FormData($('#filters'))); params.set('limit', '1000');
  params.set('ocr_backend', state.ocrBackend);
  state.records = await api(`/api/results?${params}`);
  state.selected.clear(); $('#select-all').checked = false;
  const body = $('#records-body');
  if (!state.records.length) { body.innerHTML = '<tr><td colspan="10" class="empty-state">没有符合条件的回单</td></tr>'; return; }
  body.innerHTML = state.records.map(recordRow).join('');
  $$('.record-select', body).forEach(input => input.addEventListener('change', () => toggleSelected(Number(input.value), input.checked)));
  $$('[data-open-review]', body).forEach(button => button.addEventListener('click', () => openReview(Number(button.dataset.openReview))));
  $$('[data-retry-row]', body).forEach(button => button.addEventListener('click', () => retryOne(Number(button.dataset.retryRow), button)));
}

function recordRow(item) {
  const fields = item.fields || {}, date = item.date_check || {}, seal = item.seal_check || {};
  const lowCount = Object.values(item.field_metadata || {}).filter(meta => meta.low_confidence).length;
  const error = item.error_message ? `<small class="error-text">${escapeHtml(item.error_message)}</small>` : '';
  const pageInfo = item.page_group?.page_count > 1
    ? (item.page_role === 'continuation'
      ? ` · 第 ${item.page_index + 1} 页，归属 #${item.parent_result_id}`
      : ` · 共 ${item.page_group.page_count} 页`)
    : '';
  return `<tr class="${item.review_status === '待复核' ? 'pending-row' : ''}">
    <td><input class="record-select" type="checkbox" value="${item.id}" aria-label="选择 ${escapeHtml(item.filename)}"></td>
    <td><strong>${escapeHtml(item.filename)}</strong><small>${escapeHtml(item.document_type?.label || '旧记录未分类')} · ${escapeHtml(fields['客户订单号'] || '无订单号')}${escapeHtml(pageInfo)}</small>${error}</td>
    <td>${escapeHtml(fields['客户名称'] || '—')}</td>
    <td><span>${escapeHtml(fields['要求到货'] || date.required || '—')}</span><small>${escapeHtml(date.actual || '未识别')}</small></td>
    <td>${statusPill(date.status || '未识别')}<small>置信度 ${percent(date.confidence)}</small></td>
    <td>${statusPill(seal.status || '未识别')}<small>相似度 ${percent(seal.score)}</small></td>
    <td>${statusPill(item.final_result || item.overall)}</td>
    <td>${statusPill(item.review_status)}${lowCount ? `<small class="low-note">${lowCount} 个低置信度字段</small>` : ''}</td>
    <td><span>${escapeHtml(item.updated_at || item.created_at || '—')}</span><small>${escapeHtml(item.ocr_backend_label || item.ocr_backend || '旧记录')} · 第 ${item.attempt || 1} 次识别</small></td>
    <td><div class="row-actions"><button class="mini-button" data-open-review="${item.id}">复核</button><button class="mini-button" data-retry-row="${item.id}">重试</button></div></td></tr>`;
}

function toggleSelected(id, checked) { checked ? state.selected.add(id) : state.selected.delete(id); }

async function openReview(id) {
  const item = await api(`/api/results/${id}`); state.current = item; state.artifactTab = 'date';
  const modal = $('#review-modal'), content = $('#review-content');
  content.innerHTML = ''; content.append($('#review-template').content.cloneNode(true));
  $('[name=result_id]', content).value = id; $('[data-preview]', content).src = item.preview_url || '';
  const docType = item.document_type || {};
  const pageSummary = item.page_group?.page_count > 1
    ? `<p>分页关系：${escapeHtml((item.page_group.filenames || []).join(' + '))}${item.page_role === 'continuation' ? ` · 归属结果 #${item.parent_result_id}` : ''}</p>` : '';
  const safetySummary = item.safety_policy ? `<p class="safety-policy">${escapeHtml(item.safety_policy)}</p>` : '';
  $('[data-status-summary]', content).innerHTML = `<div>${statusPill(item.final_result || item.overall)}</div><p><b>${escapeHtml(docType.label || '旧记录未分类')}</b>${docType.confidence != null ? ` · 类型置信度 ${percent(docType.confidence)}` : ''}</p>${pageSummary}${safetySummary}<p>${escapeHtml((item.review_reasons || []).join('；') || '暂无自动复核原因')}</p>`;
  $('[data-fields]', content).innerHTML = Object.entries(item.fields || {}).filter(([name]) => name !== '商品明细原文').map(([name, value]) => fieldEditor(name, value, item.field_metadata?.[name])).join('');
  renderProductTable(item.product_table, content);
  const actualDateInput = $('[name=actual_date]', content);
  actualDateInput.value = item.date_check?.actual || '';
  const rejectedDates = item.date_check?.rejected_candidates || [];
  if (rejectedDates.length) {
    const summary = rejectedDates.map(row => `${row.value}（${row.reason}；OCR：${(row.ocr_texts || []).join(' | ') || '无文本'}）`).join('；');
    actualDateInput.closest('label').insertAdjacentHTML(
      'beforeend',
      `<small class="low-note">已拒绝候选：${escapeHtml(summary)}</small>`,
    );
  }
  const overriddenDates = item.date_check?.business_time_overridden_candidates || [];
  if (overriddenDates.length) {
    const summary = overriddenDates.map(row => `${row.value}（原业务检查：${row.reason}；OCR：${(row.ocr_texts || []).join(' | ') || '无文本'}）`).join('；');
    actualDateInput.closest('label').insertAdjacentHTML(
      'beforeend',
      `<small>强跨模型共识已覆盖业务时间异常：${escapeHtml(summary)}</small>`,
    );
  }
  $('[name=seal_text]', content).value = item.seal_check?.recognized || '';
  $('[name=human_note]', content).value = item.human_note || '';
  $('[name=error_type]', content).value = item.error_type || '';
  renderArtifacts(); await Promise.all([renderHistory(), renderGroundTruth()]);
  $$('[data-artifact-tab]', content).forEach(button => button.addEventListener('click', () => { state.artifactTab = button.dataset.artifactTab; $$('[data-artifact-tab]', content).forEach(x => x.classList.toggle('active', x === button)); renderArtifacts(); }));
  $('[data-retry]', content).addEventListener('click', () => retryOne(id));
  $('[data-save-pending]', content).addEventListener('click', () => submitReview('待复核', '需人工复核'));
  $('[data-confirm-pass]', content).addEventListener('click', () => submitReview('确认通过', '通过'));
  $('[data-confirm-fail]', content).addEventListener('click', () => submitReview('确认不通过', '不通过'));
  modal.classList.remove('hidden'); document.body.classList.add('modal-open');
}

function fieldEditor(name, value, meta = {}) {
  const confidence = Number(meta.confidence ?? 0);
  const original = String(meta.original ?? '');
  const correctionNote = original && original !== String(value)
    ? `<small class="original-ocr">原始 OCR：${escapeHtml(original)}</small>` : '';
  return `<label class="field-editor ${meta.low_confidence ? 'low-confidence' : ''}"><span>${escapeHtml(name)}<em>${percent(confidence)} · ${escapeHtml(meta.source || 'OCR')}</em></span><textarea name="field:${escapeHtml(name)}" rows="${String(value).length > 45 ? 3 : 1}">${escapeHtml(value)}</textarea>${correctionNote}${meta.low_confidence ? '<small>低置信度，请人工检查</small>' : ''}</label>`;
}

function renderProductTable(table, root) {
  const section = $('[data-product-section]', root), target = $('[data-product-table]', root);
  if (!table?.rows?.length) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden');
  const columns = table.columns || [];
  target.innerHTML = `<table class="product-review-table"><thead><tr>${columns.map(name => `<th>${escapeHtml(name)}</th>`).join('')}<th>行置信度</th></tr></thead><tbody>${table.rows.map((row, rowIndex) => `<tr>${columns.map(name => {
    const value = row.values?.[name] || '', confidence = Number(row.confidences?.[name] || 0);
    const low = (row.low_confidence_columns || []).includes(name);
    const source = row.sources?.[name] || row.source || 'OCR';
    return `<td class="${low ? 'low-confidence-cell' : ''}"><input class="product-cell-input" data-product-row="${rowIndex}" data-product-column="${escapeHtml(name)}" value="${escapeHtml(value)}"><small>${value ? percent(confidence) : '未识别'} · ${escapeHtml(source)}</small></td>`;
  }).join('')}<td class="row-confidence">${percent(row.row_confidence)}</td></tr>`).join('')}</tbody></table><p class="product-table-note">表格综合置信度 ${percent(table.confidence)} · ${escapeHtml(table.source || '')}</p>`;
}

function renderArtifacts() {
  const target = $('[data-artifacts]', $('#review-content'));
  const artifacts = state.current?.processing_artifacts?.[state.artifactTab] || [];
  if (!artifacts.length) { target.innerHTML = '<div class="empty-state">没有保存的中间处理图（旧记录可重新识别生成）</div>'; return; }
  if (state.artifactTab === 'date') {
    target.innerHTML = artifacts.map(item => {
      const lineTexts = (item.date_line_ocr_variants || []).flatMap(row => row.ocr_texts || []);
      const lineInfo = item.date_line_ocr_backend
        ? ` · 日期行 ${escapeHtml(item.date_line_ocr_backend)}：${escapeHtml(lineTexts.join(' | ') || '未识别')}`
        : '';
      const lineWhiteInfo = item.date_line_white_candidate
        ? `<small>原色白底 Vision 候选：${escapeHtml(item.date_line_white_candidate)}</small><small class="low-note">${escapeHtml(item.date_line_white_acceptance_note || '')}</small>`
        : '';
      const farLowerInfo = item.far_lower_server_backend
        ? `<small>远下方跨几何复核：Mobile 完整区域 + ${escapeHtml(item.far_lower_server_backend)} 窄日期行${item.far_lower_cross_model_candidate ? ` · 可靠候选 ${escapeHtml(item.far_lower_cross_model_candidate)}` : ' · 未形成一致日期'}</small>`
        : '';
      const maxChannelMismatchInfo = item.date_max_channel_mismatch_candidate
        ? `<small>最大通道跨模型不匹配日期：${escapeHtml(item.date_max_channel_mismatch_candidate)} · ${escapeHtml(item.date_max_channel_mismatch_note || '')}</small>`
        : '';
      const maxChannelConsensusInfo = item.date_max_channel_consensus_candidate
        ? `<small>最大通道 3 单元格日期共识：${escapeHtml(item.date_max_channel_consensus_candidate)} · ${escapeHtml(item.date_max_channel_consensus_note || '')}</small>`
        : '';
      const dominantDateInfo = item.date_server_mobile_dominance_candidate
        ? `<small>Server/Mobile 跨区域主证据：${escapeHtml(item.date_server_mobile_dominance_candidate)} · ${escapeHtml(item.date_server_mobile_dominance_note || '')}</small>`
        : '';
      const repeatedServerDateInfo = item.date_repeated_server_candidate
        ? `<small>Server 三预处理完整日期：${escapeHtml(item.date_repeated_server_candidate)} · ${escapeHtml(item.date_repeated_server_note || '')}</small>`
        : '';
      const crossYearConsensusInfo = item.date_cross_year_consensus_candidate
        ? `<small>跨年完整日期双模型/双几何共识：${escapeHtml(item.date_cross_year_consensus_candidate)} · ${escapeHtml(item.date_cross_year_consensus_note || '')}</small>`
        : '';
      const componentConsensusInfo = item.date_component_consensus_candidate
        ? `<small>年月日分槽双模型共识：${escapeHtml(item.date_component_consensus_candidate)} · ${escapeHtml(item.date_component_consensus_note || '')}</small>`
        : '';
      const serverComponentConsensusInfo = item.date_server_component_candidate
        ? `<small>Server 双几何完整日期 + 固定槽位共识：${escapeHtml(item.date_server_component_candidate)} · ${escapeHtml(item.date_server_component_note || '')}</small>`
        : '';
      const daySlotConsensusInfo = item.date_day_slot_consensus_candidate
        ? `<small>两位日窄槽共识：${escapeHtml(item.date_day_slot_consensus_candidate)} · ${escapeHtml(item.date_day_slot_consensus_note || '')}</small>`
        : '';
      const missingYearSeparatorInfo = item.date_missing_year_separator_candidate
        ? `<small>漏“年”完整日期 + 去单位日槽共识：${escapeHtml(item.date_missing_year_separator_candidate)} · ${escapeHtml(item.date_missing_year_separator_note || '')}</small>`
        : '';
      const nondestructiveCrossYearInfo = item.date_cross_year_nondestructive_candidate
        ? `<small>跨年非破坏性原图共识：${escapeHtml(item.date_cross_year_nondestructive_candidate)} · ${escapeHtml(item.date_cross_year_nondestructive_note || '')}</small>`
        : '';
      const monthSlotConflictInfo = item.date_month_slot_conflict_candidate
        ? `<small>月份窄槽冲突解析：${escapeHtml(item.date_month_slot_conflict_candidate)} · 排除 ${escapeHtml(item.date_month_slot_discarded_conflict || '')} · ${escapeHtml(item.date_month_slot_conflict_note || '')}</small>`
        : '';
      const upperDateCards = item.upper_date_line_original_url
        ? `${artifactCard('盖章行手写日期', item.upper_date_line_original_url)}${artifactCard('盖章行日期去印章色后', item.upper_date_line_color_clean_url)}`
        : '';
      const slotCards = item.date_slot_year_original_url
        ? `${artifactCard('年份槽位原图', item.date_slot_year_original_url)}${artifactCard('年份槽位最大通道增强', item.date_slot_year_processed_url)}${item.date_slot_year_line_clean_url ? artifactCard('年份槽位去横线增强', item.date_slot_year_line_clean_url) : ''}${item.date_slot_year_white_url ? artifactCard('年份槽位白底标准化（Paddle 双模型）', item.date_slot_year_white_url) : ''}${item.date_slot_month_context_original_url ? artifactCard('月份上下文槽位原图', item.date_slot_month_context_original_url) : ''}${item.date_slot_month_context_processed_url ? artifactCard('月份上下文槽位最大通道增强', item.date_slot_month_context_processed_url) : ''}${item.date_slot_day_context_original_url ? artifactCard('日期上下文槽位原图', item.date_slot_day_context_original_url) : ''}${item.date_slot_day_context_processed_url ? artifactCard('日期上下文槽位最大通道增强', item.date_slot_day_context_processed_url) : ''}${item.date_slot_month_digit_original_url ? artifactCard('月份数字窄槽原图', item.date_slot_month_digit_original_url) : ''}${item.date_slot_month_digit_processed_url ? artifactCard('月份数字窄槽最大通道增强', item.date_slot_month_digit_processed_url) : ''}${item.date_slot_month_digit_line_clean_url ? artifactCard('月份数字窄槽去横线增强', item.date_slot_month_digit_line_clean_url) : ''}${artifactCard('月日联合槽位原图', item.date_slot_month_day_original_url)}${artifactCard('月日联合槽位最大通道增强', item.date_slot_month_day_processed_url)}${item.date_slot_month_day_line_clean_url ? artifactCard('月日槽位去横线增强', item.date_slot_month_day_line_clean_url) : ''}${item.date_slot_month_day_vision_url ? artifactCard('月日槽位裁后去章色白底（Vision 建议）', item.date_slot_month_day_vision_url) : ''}`
        : '';
      const daySlotCards = item.date_slot_day_digit_original_url
        ? `${artifactCard('日数字窄槽原图', item.date_slot_day_digit_original_url)}${artifactCard('日数字窄槽最大通道增强', item.date_slot_day_digit_processed_url)}${item.date_slot_day_digit_line_clean_url ? artifactCard('日数字窄槽去横线增强', item.date_slot_day_digit_line_clean_url) : ''}`
        : '';
      const innerDaySlotCards = item.date_slot_day_inner_original_url
        ? `${artifactCard('去单位日数字窄槽原图', item.date_slot_day_inner_original_url)}${artifactCard('去单位日数字窄槽最大通道增强', item.date_slot_day_inner_processed_url)}${item.date_slot_day_inner_line_clean_url ? artifactCard('去单位日数字窄槽去横线增强', item.date_slot_day_inner_line_clean_url) : ''}`
        : '';
      const slotTexts = (item.date_slot_ocr_variants || []).map(row => `${row.method}：${(row.ocr_texts || []).join(' | ') || '未识别'}`).join('；');
      const slotInfo = item.date_slot_year_original_url
        ? `<small>槽位 OCR：${escapeHtml(slotTexts)}${item.date_slot_candidate ? ` · ${item.date_slot_reliable ? '可靠候选' : '候选'} ${escapeHtml(item.date_slot_candidate)}` : ''}${item.date_slot_candidate_source ? ` · ${escapeHtml(item.date_slot_candidate_source)}` : ''}</small><small class="${item.date_slot_reliable ? '' : 'low-note'}">${escapeHtml(item.date_slot_acceptance_note || '')}</small>`
        : '';
      const daySlotTexts = (item.date_slot_day_ocr_variants || []).map(row => `${row.method}：${(row.ocr_texts || []).join(' | ') || '未识别'}`).join('；');
      const daySlotInfo = item.date_slot_day_digit_original_url
        ? `<small>日数字窄槽 OCR：${escapeHtml(daySlotTexts)}${item.date_slot_day_candidate ? ` · ${item.date_slot_day_reliable ? '可靠候选' : '候选'} ${escapeHtml(item.date_slot_day_candidate)}` : ''}</small><small class="${item.date_slot_day_reliable ? '' : 'low-note'}">${escapeHtml(item.date_slot_day_acceptance_note || '')}</small>`
        : '';
      const innerDaySlotTexts = (item.date_slot_day_inner_ocr_variants || []).map(row => `${row.method}：${(row.ocr_texts || []).join(' | ') || '未识别'}`).join('；');
      const innerDaySlotInfo = item.date_slot_day_inner_original_url
        ? `<small>去单位日数字窄槽 OCR：${escapeHtml(innerDaySlotTexts)}${item.date_slot_day_inner_candidate ? ` · ${item.date_slot_day_inner_reliable ? '可靠候选' : '候选'} ${escapeHtml(item.date_slot_day_inner_candidate)}` : ''}</small><small class="${item.date_slot_day_inner_reliable ? '' : 'low-note'}">${escapeHtml(item.date_slot_day_inner_acceptance_note || '')}</small>`
        : '';
      return `<article class="artifact-group"><h4>${escapeHtml(item.variant)} <small>OCR：${escapeHtml((item.ocr_texts || []).join(' | ') || '未识别')}${lineInfo}</small>${lineWhiteInfo}${farLowerInfo}${maxChannelMismatchInfo}${maxChannelConsensusInfo}${dominantDateInfo}${repeatedServerDateInfo}${crossYearConsensusInfo}${componentConsensusInfo}${serverComponentConsensusInfo}${daySlotConsensusInfo}${missingYearSeparatorInfo}${nondestructiveCrossYearInfo}${monthSlotConflictInfo}${slotInfo}${daySlotInfo}${innerDaySlotInfo}</h4><div class="artifact-grid">${artifactCard('原始手写日期区域', item.original_url)}${artifactCard('去除彩色印章后', item.color_clean_url)}${artifactCard('去表格线增强后', item.line_clean_url)}${artifactCard('仅手写日期行', item.date_line_original_url)}${artifactCard('日期行去印章色后', item.date_line_color_clean_url)}${item.date_line_table_clean_url ? artifactCard('日期行去表格线后', item.date_line_table_clean_url) : ''}${item.date_line_table_clean_upscaled_url ? artifactCard('日期行去表格线三倍放大', item.date_line_table_clean_upscaled_url) : ''}${item.date_line_autocontrast_upscaled_url ? artifactCard('日期行灰度自动对比三倍放大', item.date_line_autocontrast_upscaled_url) : ''}${item.date_line_max_channel_upscaled_url ? artifactCard('日期行最大通道去彩色三倍放大', item.date_line_max_channel_upscaled_url) : ''}${item.date_line_otsu_upscaled_url ? artifactCard('日期行 Otsu 二值三倍放大', item.date_line_otsu_upscaled_url) : ''}${item.date_line_white_standardized_url ? artifactCard('原日期行白底标准化（Vision 多配置）', item.date_line_white_standardized_url) : ''}${slotCards}${daySlotCards}${innerDaySlotCards}${upperDateCards}</div></article>`;
    }).join('');
  } else {
    target.innerHTML = artifacts.map(item => {
      const reference = item.visual_reference_match;
      const consensus = reference?.consensus_matches || [];
      const consensusInfo = ['multi_reference_consensus', 'high_purity_multi_reference_consensus', 'chromatic_crop_multi_reference_consensus'].includes(reference?.route)
        ? ` · 多参考一致 ${reference.consensus_reference_count || consensus.length} 份（${consensus.map(row => `${escapeHtml(row.reference_filename || '')} ${row.homography_inliers || 0}内点`).join('、')}）`
        : '';
      const regularGeometry = reference?.regular_geometry;
      const regularGeometryInfo = regularGeometry
        ? ` · 排除黑色噪声前 ${regularGeometry.homography_inliers || 0}/${regularGeometry.good_matches || 0}内点，覆盖 ${percent(Math.min(regularGeometry.candidate_coverage || 0, regularGeometry.reference_coverage || 0))}`
        : '';
      const acceptedRouteLabel = {
        multi_reference_consensus: '多参考一致通过',
        high_purity_multi_reference_consensus: '多参考高纯度一致通过',
        chromatic_crop_multi_reference_consensus: '章色稳健裁剪多参考一致通过',
        trimmed_chromatic_single_reference: '稀疏章色噪点裁剪高纯度通过',
        color_mask_geometry: '彩色墨迹整体一致通过',
        color_mask_sift_geometry: '彩色墨迹与 SIFT 联合一致通过',
        high_ratio_single_reference: '高内点率路线通过',
        high_support_minor_coverage: '高支持度微覆盖路线通过',
        ultra_support_partial_coverage: '超高支持度局部覆盖路线通过',
        branded_station_single_reference: '三星服务中心站号章结构与几何联合通过',
      }[reference?.route] || '达到单参考严格门槛';
      const referenceInfo = reference
        ? `<small class="${reference.accepted ? '' : 'low-note'}">参考章几何复核：${reference.accepted ? acceptedRouteLabel : '未达到门槛'} · 内点 ${reference.homography_inliers || 0}/${reference.good_matches || 0} · 内点率 ${percent(reference.inlier_ratio || 0)} · 章面覆盖 ${percent(Math.min(reference.candidate_coverage || 0, reference.reference_coverage || 0))}${regularGeometryInfo}${reference.color_mask_score ? ` · 彩色墨迹 ${percent(reference.color_mask_score)}（相关 ${percent(reference.color_mask_correlation || 0)} / Dice ${percent(reference.color_mask_dice || 0)}）` : ''} · 参考 ${escapeHtml(reference.reference_filename || '')}${consensusInfo}</small>`
        : '';
      const referenceCard = reference?.reference_url
        ? artifactCard(`人工真值阳性参考章 · ${reference.reference_filename}`, reference.reference_url)
        : '';
      return `<article class="artifact-group"><h4>收货印章 ${item.index + 1} · ${escapeHtml(item.color)} · ${escapeHtml(item.shape || '未知形状')} <small>增强 OCR：${escapeHtml(item.unwrapped_text || '未识别')}</small>${item.color_isolated_text || item.secondary_color_isolated_text ? `<small>保留章色 OCR：${escapeHtml(item.color_isolated_text || item.secondary_color_isolated_text)}</small>` : ''}${item.code_line_text || item.secondary_code_line_text ? `<small>编号行 OCR：${escapeHtml(item.code_line_text || item.secondary_code_line_text)}</small>` : ''}${item.rotated_text || item.secondary_rotated_text ? `<small>180° 旋转 OCR：${escapeHtml(item.rotated_text || item.secondary_rotated_text)}</small>` : ''}${item.same_region_reconstructed_text ? `<small>同章区完整片段重组：${escapeHtml(item.same_region_reconstructed_text)}</small>` : ''}${item.overlapping_region_reconstructed_text ? `<small>重叠章区精确片段重组：${escapeHtml(item.overlapping_region_reconstructed_text)}</small><small>${escapeHtml(item.overlapping_region_acceptance_note || '')}</small>` : ''}${item.conflict_mobile_band_text ? `<small>Mobile 矩形分带：${escapeHtml(item.conflict_mobile_band_text)}</small><small class="${item.conflict_mobile_band_resolution ? '' : 'low-note'}">${escapeHtml(item.conflict_mobile_band_acceptance_note || '')}</small>` : ''}${item.robust_mobile_text || item.robust_server_text ? `<small>稳健圆心 Mobile：${escapeHtml(item.robust_mobile_text || '未识别')}</small><small>稳健圆心 Server：${escapeHtml(item.robust_server_text || '未识别')}</small><small class="${item.robust_shared_suffix ? '' : 'low-note'}">${escapeHtml(item.robust_bounds_acceptance_note || '')}</small>` : ''}${item.server_audit_text ? `<small>Server 大模型复核：${escapeHtml(item.server_audit_text)}</small>` : ''}${item.server_audit_rejection_reason ? `<small class="low-note">${escapeHtml(item.server_audit_rejection_reason)}</small>` : ''}${referenceInfo}${item.combined_text ? `<small>同一区域融合证据：${escapeHtml(item.combined_text)}</small>` : ''}</h4><div class="artifact-grid">${artifactCard('印章原始区域', item.original_url)}${artifactCard('颜色分离高对比图', item.isolated_url)}${item.color_isolated_url ? artifactCard('保留章色白底图', item.color_isolated_url) : ''}${reference?.candidate_chromatic_crop_url ? artifactCard('章色稳健裁剪图（SIFT）', reference.candidate_chromatic_crop_url) : ''}${reference?.candidate_trimmed_chromatic_crop_url ? artifactCard('去稀疏噪点章色裁剪图（SIFT）', reference.candidate_trimmed_chromatic_crop_url) : ''}${item.code_line_url ? artifactCard('矩形编号章数字行', item.code_line_url) : ''}${artifactCard(item.shape === '矩形' ? '矩形印章校正图' : '圆章环形文字展开图', item.unwrapped_url)}${(item.unwrapped_band_urls || []).map((url, index) => artifactCard(`圆章展开独立分带 ${index + 1}`, url)).join('')}${item.robust_unwrapped_url ? artifactCard('稳健边界圆章展开图', item.robust_unwrapped_url) : ''}${(item.robust_unwrapped_band_urls || []).map((url, index) => artifactCard(`稳健圆心独立分带 ${index + 1}`, url)).join('')}${(item.conflict_mobile_band_urls || []).map((url, index) => artifactCard(`矩形章横向分带 ${index + 1}`, url)).join('')}${item.unwrapped_rotated_url ? artifactCard('圆章展开 180° 图', item.unwrapped_rotated_url) : ''}${item.color_isolated_rotations_url ? artifactCard('保留章色旋转对照图', item.color_isolated_rotations_url) : ''}${item.rotated_url ? artifactCard('矩形印章 180° 旋转图', item.rotated_url) : ''}${referenceCard}</div></article>`;
    }).join('');
  }
}

function artifactCard(label, url) { return `<figure><a href="${url}" target="_blank"><img src="${url}" alt="${escapeHtml(label)}" loading="lazy"></a><figcaption>${escapeHtml(label)}</figcaption></figure>`; }

async function renderHistory() {
  const history = await api(`/api/results/${state.current.id}/review-history`);
  $('[data-history]', $('#review-content')).innerHTML = history.length ? history.map(row => `<div class="history-item"><b>${escapeHtml(row.action)}</b><span>${escapeHtml(row.changed_at)}</span><p>${escapeHtml(historySummary(row))}</p></div>`).join('') : '<div class="empty-state">尚无人工修改记录</div>';
}

async function renderGroundTruth() {
  const truth = await api(`/api/results/${state.current.id}/ground-truth`);
  const root = $('#review-content');
  $('[data-truth-status]', root).textContent = truth.exists ? '该样本已有真值，可审计更新' : '尚未标注，可在确认时新增';
  $('[name=truth_seal_should_match]', root).value = truth.exists
    ? String(Boolean(truth.entry?.seal_should_match)) : '';
  const history = truth.history || [];
  $('[data-truth-history]', root).innerHTML = history.length
    ? `<small>最近真值记录：${escapeHtml(history[0].action)} · ${escapeHtml(history[0].changed_at)}</small>`
    : '<small>尚无真值修改历史</small>';
}

function historySummary(row) {
  try {
    const before = JSON.parse(row.before_json || '{}'), after = JSON.parse(row.after_json || '{}'), changed = [];
    const oldFields = before.fields || {}, newFields = after.fields || {};
    Object.keys({...oldFields, ...newFields}).forEach(key => { if ((oldFields[key] || '') !== (newFields[key] || '')) changed.push(`${key}: ${oldFields[key] || '空'} → ${newFields[key] || '空'}`); });
    const oldDate = before.date_check?.actual || '', newDate = after.date_check?.actual || '';
    if (oldDate !== newDate) changed.push(`实际日期: ${oldDate || '空'} → ${newDate || '空'}`);
    const oldSeal = before.seal_check?.recognized || '', newSeal = after.seal_check?.recognized || '';
    if (oldSeal !== newSeal) changed.push(`印章: ${oldSeal || '空'} → ${newSeal || '空'}`);
    const oldProductRows = before.product_table?.rows || [], newProductRows = after.product_table?.rows || [];
    newProductRows.forEach((row, index) => Object.keys(row.values || {}).forEach(column => {
      const oldValue = oldProductRows[index]?.values?.[column] || '', newValue = row.values?.[column] || '';
      if (oldValue !== newValue) changed.push(`商品第 ${index + 1} 行 ${column}: ${oldValue || '空'} → ${newValue || '空'}`);
    }));
    return [row.note, row.error_type, ...changed].filter(Boolean).join('；') || '状态已更新';
  } catch (_) { return row.note || row.error_type || '状态已更新'; }
}

async function submitReview(reviewStatus, finalResult) {
  const form = $('#review-form'), fields = {};
  $$('[name^="field:"]', form).forEach(input => fields[input.name.slice(6)] = input.value);
  const productRows = $$('.product-cell-input', form).map(input => ({row: Number(input.dataset.productRow), column: input.dataset.productColumn, value: input.value}));
  const saveGroundTruth = form.save_ground_truth.checked;
  if (saveGroundTruth && reviewStatus === '待复核') return toast('评测真值只能在确认通过或确认不通过时保存', 'warning');
  if (saveGroundTruth && !['true', 'false'].includes(form.truth_seal_should_match.value)) return toast('请人工选择印章真值结论', 'warning');
  if (reviewStatus === '确认通过' && (!form.actual_date.value || !form.seal_text.value.trim())) {
    return toast('确认通过前请填写并核对实际收货日期和印章内容', 'danger');
  }
  const truthSealShouldMatch = form.truth_seal_should_match.value === '' ? null : form.truth_seal_should_match.value === 'true';
  const payload = {fields, product_rows: productRows, actual_date: form.actual_date.value, seal_text: form.seal_text.value, error_type: form.error_type.value, human_note: form.human_note.value, review_status: reviewStatus, final_result: finalResult, action: reviewStatus, save_ground_truth: saveGroundTruth, truth_seal_should_match: truthSealShouldMatch};
  try {
    state.current = await api(`/api/results/${state.current.id}/review`, {method: 'PATCH', json: payload});
    toast(state.current.ground_truth_saved ? `复核与评测真值已保存（共 ${state.current.ground_truth_saved.total} 张标注样本）` : '复核结果和修改历史已保存', 'success'); closeModal(); await Promise.all([loadRecords(), loadReport()]);
  } catch (error) {
    toast(error.message, 'danger');
  }
}

async function retryOne(id, button) {
  if (button) button.disabled = true;
  try { await api(`/api/results/${id}/retry`, {method: 'POST', json: {ocr_backend: state.ocrBackend}}); toast('重新识别完成，中间过程图已更新', 'success'); closeModal(); await Promise.all([loadRecords(), loadReport()]); }
  catch (error) { toast(error.message, 'danger'); }
  finally { if (button) button.disabled = false; }
}

async function bulkReview(reviewStatus, finalResult) {
  if (!state.selected.size) return toast('请先勾选回单', 'warning');
  if (reviewStatus === '确认通过') {
    const unsafe = state.records.filter(item => state.selected.has(Number(item.id))).filter(item => {
      const date = item.date_check || {}, seal = item.seal_check || {};
      return !date.actual || date.status !== '匹配' || date.reliable !== true || !seal.recognized || seal.status !== '匹配' || seal.reliable !== true;
    });
    if (unsafe.length) return toast(`有 ${unsafe.length} 张日期或印章证据不完整，不能批量确认通过`, 'danger');
  }
  try {
    await api('/api/results/bulk-review', {method: 'POST', json: {ids: [...state.selected], review_status: reviewStatus, final_result: finalResult}});
    toast(`已批量更新 ${state.selected.size} 张回单`, 'success'); await Promise.all([loadRecords(), loadReport()]);
  } catch (error) {
    toast(error.message, 'danger');
  }
}

async function bulkRetry() {
  if (!state.selected.size) return toast('请先勾选回单', 'warning');
  const output = await api('/api/results/bulk-retry', {method: 'POST', json: {ids: [...state.selected], ocr_backend: state.ocrBackend}});
  const failed = output.filter(item => !item.ok).length;
  toast(`批量重新识别完成：成功 ${output.length - failed}，失败 ${failed}`, failed ? 'warning' : 'success'); await Promise.all([loadRecords(), loadReport()]);
}

async function loadReport() {
  const report = await api(`/api/report?ocr_backend=${encodeURIComponent(state.ocrBackend)}`), accuracy = report.accuracy || {}, dataset = report.dataset || {};
  const scope = $('#accuracy-scope');
  if (scope) scope.textContent = `${accuracy.scope_backend_label || '当前后端'} · 已测试 ${accuracy.tested_samples || 0}/${accuracy.ground_truth_samples || 0} 张真值样本`;
  const cards = [
    ['字段准确率', percent(accuracy.field_accuracy), `${accuracy.field_correct || 0} / ${accuracy.field_total || 0} 个样单字段`],
    ['商品单元格准确率', percent(accuracy.product_cell_accuracy), `整行 ${accuracy.product_row_correct || 0} / ${accuracy.product_row_total || 0}`],
    ['日期识别准确率', percent(accuracy.date_accuracy), `检出率 ${percent(accuracy.date_detection_rate)}`],
    ['印章结论准确率', percent(accuracy.seal_conclusion_accuracy), `平均相似度 ${percent(accuracy.seal_average_similarity)}`],
    ['待人工复核', report.review_counts?.['待复核'] || 0, `总记录 ${report.total_results || 0}`],
    ['评测真值覆盖', percent(dataset.coverage), `${dataset.labeled || 0} / ${dataset.images || 0} 张数据图片`],
  ];
  $('#kpi-grid').innerHTML = cards.map(([label, value, note]) => `<div class="kpi-card"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  $('#error-stats').innerHTML = (report.error_types || []).length ? report.error_types.map(item => `<div><span>${escapeHtml(item.type)}</span><b>${item.count}</b></div>`).join('') : '<div class="empty-state">暂无错误记录</div>';
  $('#field-accuracy').innerHTML = (accuracy.field_by_name || []).map(item => `<div><span>${escapeHtml(item.field)}</span><i><em style="width:${item.accuracy * 100}%"></em></i><b>${percent(item.accuracy)}</b></div>`).join('');
  $('#product-accuracy').innerHTML = (accuracy.product_by_column || []).map(item => `<div><span>${escapeHtml(item.column)}</span><i><em style="width:${item.accuracy * 100}%"></em></i><b>${percent(item.accuracy)}</b></div>`).join('');
  $('#backend-comparison').innerHTML = (report.backend_comparison || []).map(item => `<article><div><strong>${escapeHtml(item.label)}</strong><small>${item.samples} 张 · 平均 ${item.average_seconds} 秒/张</small></div><dl><div><dt>字段</dt><dd>${percent(item.field_accuracy)}</dd></div><div><dt>商品</dt><dd>${percent(item.product_cell_accuracy)}</dd></div><div><dt>日期</dt><dd>${percent(item.date_accuracy)}</dd></div><div><dt>印章</dt><dd>${percent(item.seal_conclusion_accuracy)}</dd></div></dl></article>`).join('') || '<div class="empty-state">暂无完整后端评测</div>';
}

function closeModal() { $('#review-modal').classList.add('hidden'); document.body.classList.remove('modal-open'); state.current = null; }
$$('[data-close-modal]').forEach(node => node.addEventListener('click', closeModal));
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeModal(); });

async function api(url, options = {}) {
  const config = {...options};
  if (config.json !== undefined) { config.headers = {'Content-Type': 'application/json', ...(config.headers || {})}; config.body = JSON.stringify(config.json); delete config.json; }
  const response = await fetch(url, config); const type = response.headers.get('content-type') || '';
  const data = type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(data.error || data.detail || `请求失败 (${response.status})`);
  return data;
}

function statusPill(status) { return `<span class="pill ${statusClass(status)}">${escapeHtml(status || '—')}</span>`; }
function updateBackendDetail() {
  const option = ocrBackendSelect.selectedOptions[0];
  const safety = option?.dataset.safetyPolicy ? `；安全策略：${option.dataset.safetyPolicy}` : '';
  const usage = option?.dataset.usageNote ? `；建议：${option.dataset.usageNote}` : '';
  const serverLimit = option?.dataset.maxPageSide ? `；Server 整页最长边：${option.dataset.maxPageSide}px（可配置）` : '';
  if (state.ocrBackend.startsWith('hybrid')) {
    $('#ocr-backend-detail').textContent = `表单/商品：${option?.dataset.routePage}；日期/印章：${option?.dataset.routeDate}；模型目录：${option?.dataset.modelHome}${serverLimit}${safety}${usage}`;
  } else if (state.ocrBackend.startsWith('paddle')) {
    $('#ocr-backend-detail').textContent = `模型：${option?.dataset.modelName || 'PP-OCRv5'} · ${option?.dataset.modelHome || '本地缓存目录'}（可通过 PADDLE_MODEL_HOME 修改）${serverLimit}${safety}${usage}`;
  } else {
    $('#ocr-backend-detail').textContent = `使用 macOS 系统 Vision；仅在 macOS 可用${usage}`;
  }
  if (option) $('.engine-card small').textContent = `${option.textContent.replace('（不可用）', '')} · SQLite 审计留痕`;
}
function statusClass(status) { if (['通过', '匹配', '确认通过', '无需复核'].includes(status)) return 'success'; if (['不通过', '不匹配', '确认不通过', '识别失败'].includes(status)) return 'danger'; return 'warning'; }
function percent(value) { return `${Math.round(Number(value || 0) * 100)}%`; }
function escapeHtml(value) { const node = document.createElement('span'); node.textContent = String(value ?? ''); return node.innerHTML; }
let toastTimer;
function toast(message, type = '') { const node = $('#toast'); node.textContent = message; node.className = `toast ${type}`; clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.add('hidden'), 3500); }

Promise.all([loadRecords(), loadReport()]).catch(error => toast(error.message, 'danger'));
