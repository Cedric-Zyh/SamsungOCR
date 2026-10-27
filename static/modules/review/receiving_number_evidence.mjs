const list = value => Array.isArray(value) ? value : [];
const objects = value => list(value).filter(item => item && typeof item === 'object' && !Array.isArray(item));
const text = value => typeof value === 'string' ? value : '';
const confidenceText = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
  ? `${Math.round(value * 1000) / 10}%` : '置信度未记录';

function statusText(status) {
  if (status === 'adopted') return '圈号已并入同一用途行，最终结论仍按完整印章文字核对。';
  if (status === 'conflict') return '圈号读数或原用途行存在分歧，未并入用途文字。';
  if (status === 'unverified') return '圈号证据尚未完整确认，未并入用途文字。';
  if (status === 'not_detected') return '未检测到可采用的右侧圈号，保留原用途文字。';
  return '圈号补读结果待核对，未并入用途文字。';
}

function explanation(reason) {
  if (reason === 'same_row_two_color_reads_agree') return '同一印章、同一用途行右侧的两路原色补读一致。';
  if (reason === 'existing_identity_number_or_purpose_conflict') return '已有章号或用途文字存在分歧，补读没有覆盖原读数。';
  if (reason === 'managed_identity_not_adopted_for_same_row') return '收货章身份或用途行尚未确认，圈号仅保留供核对。';
  if (reason === 'complete_same_stamp_rows_not_consistent') return '同章完整文字行尚未形成一致证据，圈号仅保留供核对。';
  if (reason === 'two_literal_circle_reads_not_reliable_and_equal') return '两路补读未得到可靠且相同的圈号。';
  if (['circle_read_provenance_not_verified', 'selected_identity_not_bare_purpose'].includes(reason)) {
    return '圈号与原用途行的对应关系尚未完整确认。';
  }
  return '';
}

export function renderReceivingNumberEvidence(item, {artifactCard, escapeHtml} = {}) {
  const evidence = item?.receiving_number;
  if (!evidence || typeof evidence !== 'object' || Array.isArray(evidence)
      || typeof artifactCard !== 'function' || typeof escapeHtml !== 'function') return '';
  const recordedInputs = objects(evidence.inputs);
  const inputs = recordedInputs.length ? recordedInputs
    : objects(item.inputs).filter(input => input.channel === 'receiving_number');
  const reads = objects(item.reads);
  const hasId = input => typeof input.id === 'string' && input.id.length > 0;
  const inputReads = input => hasId(input) ? reads.filter(read => read.input_id === input.id) : [];
  const rawText = values => values.length ? values.map(read =>
    `${read.status === 'failed' ? '识别失败 · ' : ''}${text(read.text) || '未读到文字'} · ${confidenceText(read.confidence)}`
  ).join(' / ') : '未读到文字';
  const inputIds = new Set(inputs.filter(hasId).map(input => input.id));
  const orphanReads = reads.filter(read => read.channel === 'receiving_number' && !inputIds.has(read.input_id));
  if (!inputs.length && !orphanReads.length && !text(evidence.status)) return '';
  const cards = inputs.map(input => {
    const scale = input.number_scale === 1 ? '原尺寸' : input.number_scale === 2 ? '2倍' : '尺寸未记录';
    const label = `右侧圈号 · ${scale}${input.representation === 'color' ? ' · 原色' : ''}`;
    const recognized = `${input.status === 'failed' ? '本次识别失败 · ' : ''}${rawText(inputReads(input))}`;
    // Image URL validation and escaping remain in the shared artifact renderer.
    return artifactCard(label, input.image_url, recognized, false)
      || `<div class="seal-source-results"><small>${escapeHtml(label)} · 处理图不可用</small><div class="seal-source-row"><span>${escapeHtml(recognized)}</span></div></div>`;
  }).join('');
  const row = (label, value) => value
    ? `<div class="seal-source-row"><b>${escapeHtml(label)}</b><span>${escapeHtml(value)}</span></div>` : '';
  const originals = list(evidence.original_purpose_texts).filter(value => typeof value === 'string');
  const location = Number.isInteger(evidence.row_index) && evidence.row_index >= 0 ? `第${evidence.row_index + 1}格` : '';
  const summary = row('用途所在格', location)
    + originals.map(value => row('原用途读数', value || '未读到文字')).join('')
    + row('补读圈号', text(evidence.number_text))
    + row(evidence.status === 'adopted' ? '并入后的用途文字' : '待核对用途候选（未采用）', text(evidence.combined_purpose_text));
  const note = explanation(evidence.reason);
  const orphanMarkup = orphanReads.length ? `<div class="seal-source-results">${row('未关联补读图的原始读数', rawText(orphanReads))}</div>` : '';
  return `<section class="seal-input-chain receiving-number-evidence"><h5>右侧圈号补读</h5><small${evidence.status === 'adopted' ? '' : ' class="low-note"'}>${escapeHtml(statusText(evidence.status))}</small>${note ? `<small>${escapeHtml(note)}</small>` : ''}${summary ? `<div class="seal-source-results">${summary}</div>` : ''}${cards ? `<div class="artifact-grid">${cards}</div>` : ''}${orphanMarkup}</section>`;
}
