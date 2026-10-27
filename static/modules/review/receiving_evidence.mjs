const receivingChannels = new Set([
  'receiving_primary', 'receiving_detection', 'receiving_grid', 'receiving_split',
]);

const list = value => Array.isArray(value) ? value : [];
const objects = value => list(value).filter(item => item && typeof item === 'object' && !Array.isArray(item));
const rowIndex = value => Number.isInteger(value) && value >= 0 ? value : null;
const confidenceText = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
  ? `${Math.round(value * 1000) / 10}%` : '置信度未记录';

function explanation(reason) {
  if (reason === 'usable_primary_retained') return '逐行读取的身份文字已保留，是否符合签章要求仍按严格文字核对。';
  if (reason === 'insufficient_primary_recovered_without_character_replacement') return '完整格线补读形成一致身份文字，保留原始读数供核对。';
  if (['primary_meaningful_conflict', 'reliable_fragment_conflict', 'older_reliable_fragment_conflict'].includes(reason)) {
    return '仓库名、代码或章号存在不同可靠读数，未用新候选覆盖原文字。';
  }
  if (['no_unique_verified_grid_identity', 'unanchored_reliable_primary_not_overwritten'].includes(reason)) {
    return '未形成唯一、完整且一致的收货章身份文字，请核对原图。';
  }
  return '依据实际用途行区分身份文字与填写栏；证据不足或有分歧时保留待复核。';
}

function fieldIndexes(rows) {
  return new Set(list(rows).map(row => rowIndex(typeof row === 'number' ? row : row?.row_index ?? row?.rectangle_row_index))
    .filter(value => value != null));
}

function isFormField(input, reads, form) {
  if (input.channel === 'receiving_split') return false;
  if (input.form_field === true || ['receiving_form_field', 'empty_receiving_field'].includes(input.rectangle_field_kind)) return true;
  if (reads.some(read => ['receiving_form_field', 'empty_receiving_field'].includes(read.rectangle_field_kind))) return true;
  const index = rowIndex(input.rectangle_row_index ?? input.row_index);
  if (index == null) return false;
  if (input.channel === 'receiving_grid') {
    const angle = input.receiving_direction ?? input.direction;
    const direction = objects(form.grid?.directions).find(item => item.direction === angle);
    return fieldIndexes(direction?.decision?.form_rows).has(index);
  }
  return fieldIndexes(form.primary?.form_rows ?? form.primary?.decision?.form_rows ?? form.field_rows).has(index);
}

function inputLabel(input, reads, form) {
  const index = rowIndex(input.rectangle_row_index ?? input.row_index);
  const cell = index == null ? '' : ` · 第${index + 1}格`;
  const view = ['gray', 'chroma'].includes(input.representation) ? '色差灰度' : '彩色';
  if (input.channel === 'receiving_split') {
    const part = input.split_part === 'left' ? '仓库名分段' : input.split_part === 'right' ? '完整代码分段' : '分段补读';
    return `${part} · ${view}${input.split_scale === 2 ? ' · 2倍' : ''}`;
  }
  const base = input.channel === 'receiving_primary' ? '逐格补读'
    : input.channel === 'receiving_detection' ? '同格文字框复读' : '完整格线补读';
  const angle = input.receiving_direction ?? input.direction;
  const direction = input.channel === 'receiving_grid' && angle === 180 ? ' · 180°方向对照' : '';
  return `${base}${cell}${input.channel === 'receiving_grid' ? ` · ${view}` : ''}${direction}${isFormField(input, reads, form) ? ' · 填写栏（不参与匹配）' : ''}`;
}

function conflictMarkup(conflicts, escapeHtml) {
  return objects(conflicts).map(conflict => {
    const index = rowIndex(conflict.row_index);
    const heading = index == null ? '保留的不同读数' : `第${index + 1}格的不同读数`;
    const values = [conflict, ...objects(conflict.observations), ...objects(conflict.pairs).flatMap(pair => [pair.a, pair.b])]
      .filter(row => row && typeof row.text === 'string' && row.text);
    const unique = [...new Map(values.map(row => [`${row.text}|${row.confidence}`, row])).values()];
    const text = unique.length ? unique.map(row => `${row.text} · ${confidenceText(row.confidence)}`).join(' / ')
      : '此格未形成完整一致文字，保留待复核';
    return `<div class="seal-source-row"><b>${escapeHtml(heading)}</b><span>${escapeHtml(text)}</span></div>`;
  }).join('');
}

export function renderReceivingEvidence(item, {artifactCard, escapeHtml} = {}) {
  const form = item?.receiving_form;
  if (!form || typeof form !== 'object' || Array.isArray(form)
      || typeof artifactCard !== 'function' || typeof escapeHtml !== 'function') return '';
  const inputs = objects(item.inputs);
  const reads = objects(item.reads);
  const inputReads = input => reads.filter(read => read.input_id === input.id);
  const rawText = values => values.length ? values.map(read => `${read.text || '未读到文字'} · ${confidenceText(read.confidence)}`).join(' / ') : '未读到文字';
  const card = input => {
    const values = inputReads(input);
    const label = inputLabel(input, values, form);
    const text = input.status === 'failed' ? '本次识别失败' : rawText(values);
    const rendered = artifactCard(label, input.image_url, text, input.representation === 'gray');
    // An unavailable/filtered image must not hide its actual OCR readings.
    return rendered || `<div class="seal-source-results"><small>${escapeHtml(label)} · 处理图不可用</small><div class="seal-source-row"><span>${escapeHtml(text)}</span></div></div>`;
  };
  const groupInputs = values => {
    const grouped = new Map();
    for (const input of values) {
      const index = rowIndex(input.rectangle_row_index ?? input.row_index);
      const key = index == null ? '未定位格' : `第${index + 1}格`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(input);
    }
    return [...grouped].map(([label, members]) => `<details open><summary>${escapeHtml(label)}</summary><div class="artifact-grid">${members.map(card).join('')}</div></details>`).join('');
  };
  const active = inputs.filter(input => receivingChannels.has(input.channel));
  const primary = active.filter(input => ['receiving_primary', 'receiving_detection'].includes(input.channel));
  const gridInputs = active.filter(input => input.channel === 'receiving_grid');
  const splitInputs = active.filter(input => input.channel === 'receiving_split');
  const split = form.split;
  const adopted = form.managed === true && form.status === 'adopted' && Boolean(form.identity_text);
  const conflicted = form.status === 'conflict';
  const status = adopted ? '已采用收货章身份文字' : conflicted ? '身份读数冲突，需人工核对' : '身份文字证据不足，需人工核对';
  const source = form.selected_source === 'primary' ? '逐行识别'
    : form.selected_source === 'verified_grid_recovery' ? '完整格线补读' : '补充识别';
  const selected = form.identity_text
    ? `<div class="seal-source-row"><b>${adopted ? '采用的身份文字' : '保留的身份文字'}</b><span>${escapeHtml(form.identity_text)}</span></div>` : '';
  const compact = value => String(value || '').replace(/\s+/gu, '');
  const candidate = form.candidate_text && compact(form.candidate_text) !== compact(form.identity_text)
    ? `<div class="seal-source-row"><b>待核对候选</b><span>${escapeHtml(form.candidate_text)}</span></div>` : '';
  const resolution = form.requirement_code_resolution;
  const codeNote = adopted && resolution?.accepted === true && resolution.policy === 'warehouse_code_zero_o_from_requirement'
    ? `<small>仓库代码中的 0/O 已按签章要求判定；下方保留实际识别读数供核对。</small>` : '';
  const punctuation = form.requirement_title_punctuation;
  const punctuationNote = punctuation?.applied === true && punctuation.policy === 'warehouse_title_punctuation_from_requirement'
    ? '<small>首行仓库代码前的多余分隔点已按签章要求剔除；下方保留原始读数，其他字符仍独立核对。</small>' : '';
  const summary = `<div class="seal-source-results"><small class="${adopted ? '' : 'low-note'}">${escapeHtml(status)}${adopted ? ` · ${escapeHtml(source)}` : ''}</small><small>${escapeHtml(explanation(form.reason))}</small>${codeNote}${punctuationNote}${selected}${candidate}${conflictMarkup(form.primary?.conflicts, escapeHtml)}</div>`;
  const primarySection = primary.length
    ? `<details open><summary>逐行补读与填写栏</summary>${groupInputs(primary)}</details>` : '';
  const wholeGrid = `${artifactCard('完整格线章图 · 彩色', form.grid?.whole_color_url)}${artifactCard('完整格线章图 · 色差灰度', form.grid?.whole_gray_url, '', true)}`;
  const directions = [...new Set(gridInputs.map(input => input.receiving_direction ?? input.direction))];
  const gridSection = gridInputs.length || wholeGrid ? `<details><summary>完整格线补读与方向核对</summary><div class="artifact-grid">${wholeGrid}</div>${directions.map(angle => {
    const label = angle === 180 ? '180°方向对照' : angle === 0 ? '原方向' : '格线识别';
    return `<details${angle === 0 ? ' open' : ''}><summary>${label}</summary>${groupInputs(gridInputs.filter(input => (input.receiving_direction ?? input.direction) === angle))}</details>`;
  }).join('')}</details>` : '';
  const splitWarning = (split?.requires_review && (split?.accepted || list(split.conflicts).length))
    || (split?.accepted && split?.matching_allowed !== true);
  const splitText = split?.identity_text || split?.title_text || '';
  const splitSection = split || splitInputs.length ? `<section class="seal-input-chain"><h5>仓库名与完整代码分段补读</h5>${splitWarning
    ? '<small class="low-note">分段补读存在分歧，需人工核对</small>'
    : split?.accepted ? '<small>分段文字保留为识别证据，最终结论仍按完整身份核对。</small>'
      : '<small>分段补读未形成完整一致候选，保留原识别结果。</small>'}${splitText
      ? `<div class="seal-source-results"><div class="seal-source-row"><b>分段候选${splitWarning ? '（未自动采用）' : ''}</b><span>${escapeHtml(splitText)}</span></div>${conflictMarkup(split?.conflicts, escapeHtml)}</div>` : ''}<details open><summary>分段实际输入与原始读数</summary><div class="artifact-grid">${splitInputs.map(card).join('')}</div></details></section>` : '';
  const oldInputs = inputs.filter(input => !receivingChannels.has(input.channel));
  const oldCards = [artifactCard('原始章区（保留核对）', item.original_url),
    artifactCard('原整章处理图（保留核对）', item.color_isolated_oriented_url || item.color_isolated_url),
    ...oldInputs.map((input, index) => artifactCard(`原识别输入 ${index + 1}`, input.image_url, rawText(inputReads(input))))].join('');
  const represented = new Set(inputs.map(input => input.image_url));
  const legacyRows = list(item.rectangular_row_urls).filter(url => !represented.has(url)).map((url, index) =>
    artifactCard(`原文字行 ${index + 1}`, url, list(item.rectangular_row_texts)[index] || '')).join('');
  const oldReads = reads.filter(read => !receivingChannels.has(read.channel));
  const oldRaw = oldReads.map(read => `<div class="seal-source-row"><b>原识别读数</b><span>${escapeHtml(read.text || '未读到文字')} · ${escapeHtml(confidenceText(read.confidence))}</span></div>`).join('');
  const oldSection = oldCards || legacyRows || oldRaw ? `<details class="artifact-debug"><summary>原图与原识别（保留核对）</summary><div class="artifact-grid">${oldCards}${legacyRows}</div>${oldRaw ? `<div class="seal-source-results">${oldRaw}</div>` : ''}</details>` : '';
  return `<section class="seal-input-chain receiving-evidence"><h5>收货章身份与填写栏</h5><small>仓库名、代码、用途和章号作为身份文字；收货人、日期等填写栏不参与印章文字匹配，日期值无需读出或核对。所有读数仍保留供核对。</small>${summary}${primarySection}${gridSection}${splitSection}${oldSection}</section>`;
}
