const objects = value => Array.isArray(value)
  ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) : [];
const failedStatuses = new Set([
  'failed', 'failure', 'error', '识别失败', '执行失败', '失败', '错误', '未运行', '未执行', '已跳过',
]);
const purposeReading = /^(收[^①-⑳()（）\d０-９]{1,4}专用章)([①-⑳]|\([0-9０-９]{1,3}\)|（[0-9０-９]{1,3}）)?$/u;

function reliableObservation(value) {
  return value && typeof value.text === 'string' && !value.simulated && !value.error
    && !failedStatuses.has(value.status)
    && typeof value.confidence === 'number' && Number.isFinite(value.confidence)
    && value.confidence >= .75 && value.confidence <= 1;
}

export function receivingObservedComponents(artifact) {
  const form = artifact?.receiving_form;
  const primary = form?.primary;
  const index = primary?.purpose_row_index;
  if (form?.managed !== true || !Number.isInteger(index) || index < 0
      || [artifact, form, primary].some(value => value?.simulated || failedStatuses.has(value?.status))) return null;
  const rows = [...objects(primary.identity_rows), ...objects(primary.conflicts)]
    .filter(row => row.row_index === index && !row.simulated && !row.error && !failedStatuses.has(row.status));
  const observations = rows.flatMap(row => [
    ...objects(row.observations),
    ...objects(row.pairs).flatMap(pair => [pair.a, pair.b]),
  ]).filter(reliableObservation);
  const numbers = new Set(), purposes = new Set();
  for (const observation of observations) {
    // Only this stamp's observed purpose row supplies a display component.
    // Do not use the requested number, form values, or a different OCR row.
    const match = observation.text.replace(/\s+/gu, '').match(purposeReading);
    if (!match) continue;
    purposes.add(match[1]);
    if (match[2]) numbers.add(match[2]);
  }
  if (!numbers.size) return null;
  const resolvedRow = objects(primary.identity_rows).find(row => row.row_index === index);
  const resolution = resolvedRow?.purpose_resolution;
  const adoptedPurpose = resolution?.policy === 'observed_receiving_purpose' && resolution.accepted === true
    && resolution.row_index === index && resolution.purpose_text === '收货专用章'
    && purposes.has('收货专用章') && resolution.text === resolution.purpose_text + resolution.number_text
    && resolvedRow.text === resolution.text && numbers.size === 1
    && numbers.has(resolution.number_text);
  return {
    rowIndex: index,
    numbers: [...numbers],
    numberStatus: numbers.size > 1 ? 'conflict' : 'observed',
    purposeTexts: adoptedPurpose ? [resolution.purpose_text] : [...purposes],
    purposeConflict: !adoptedPurpose && purposes.size > 1,
    ...(adoptedPurpose ? {purposeAdopted: true} : {}),
  };
}

export function renderReceivingObservedComponents(artifact, {escapeHtml} = {}) {
  if (typeof escapeHtml !== 'function') return '';
  const components = receivingObservedComponents(artifact);
  if (!components) return '';
  const row = (label, values) => `<div class="seal-source-row"><b>${label}</b><span>${escapeHtml(values.join(' / '))}</span></div>`;
  return row(components.numberStatus === 'conflict' ? '编号待核对' : '已识别编号', components.numbers)
    + (components.purposeAdopted ? row('用途文字（已采用）', components.purposeTexts)
      : components.purposeConflict ? row('用途文字待核对', components.purposeTexts) : '');
}
