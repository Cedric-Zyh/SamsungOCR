// Metrics use the server's evaluated states. A seal annotation is an expected
// decision, never a character-by-character transcription of the stamp.
const SCORED = new Set(['正确', '错误', '未识别', '失败']);
const BAD = new Set(['错误', '未识别', '失败']);
const DECISIONS = new Set(['匹配', '不匹配', '部分匹配']);
const PENDING = new Set(['queued', 'running', 'awaiting_upload']);
const TERMINAL = new Set(['succeeded', 'failed']);
const STATES = ['正确', '错误', '未识别', '失败', '待标注', '未运行', '未测试', '测试中'];

export function getCell(row, key, tab = 'seal') {
  const source = tab === 'date' || key === 'date' ? row?.date : row?.seals?.[key];
  return {
    state: source?.state || (row?.job_status ? '未运行' : '未测试'),
    value: source?.value ?? '', decision: source?.decision ?? '', note: source?.note ?? '',
  };
}

export function rowAnnotated(row, tab = 'seal') {
  return tab === 'date' ? row?.expected_date !== null && row?.expected_date !== undefined
    : ['匹配', '不匹配'].includes(row?.expected_seal);
}

function rowCells(row, tab) {
  return tab === 'date' ? [getCell(row, 'date', tab)] : Object.keys(row?.seals || {}).map(key => getCell(row, key));
}

export function summarizeCoverage(rows = [], tab = 'seal') {
  const result = {total: rows.length, tested: 0, untested: 0, pending: 0, failed: 0,
    annotated: 0, unannotated: 0, evaluable: 0, anomalies: 0, positive: 0, negative: 0};
  for (const row of rows) {
    if (TERMINAL.has(row.job_status)) result.tested++;
    if (!row.job_status) result.untested++;
    if (PENDING.has(row.job_status)) result.pending++;
    if (row.job_status === 'failed') result.failed++;
    if (rowAnnotated(row, tab)) {
      result.annotated++;
      if (tab === 'date' ? !!row.expected_date : row.expected_seal === '匹配') result.positive++;
      else result.negative++;
    } else result.unannotated++;
    const cells = rowCells(row, tab);
    if (cells.some(cell => SCORED.has(cell.state))) result.evaluable++;
    if (cells.some(cell => BAD.has(cell.state))) result.anomalies++;
  }
  return result;
}

function cellSummary(cells) {
  const counts = Object.fromEntries(STATES.map(state => [state, 0]));
  for (const cell of cells) counts[cell.state] = (counts[cell.state] || 0) + 1;
  const total = cells.filter(cell => SCORED.has(cell.state)).length;
  return {...counts, counts, total, accuracy: total ? counts['正确'] / total : null,
    correct: counts['正确'], incorrect: counts['错误'], missing: counts['未识别'], failed: counts['失败'],
    unscored: cells.length - total};
}

export function summarizeChannel(rows = [], key, tab = 'seal') {
  const summary = {key, ...cellSummary(rows.map(row => getCell(row, key, tab)))};
  const errorTypes = {falseAccept: 0, falseReject: 0, partial: 0, other: 0,
    dateWrong: 0, dateMissing: 0, blankFalseRead: 0, failure: 0};
  for (const row of rows) {
    const cell = getCell(row, key, tab);
    // The annotation and an evaluated result are required for error analysis.
    if (!rowAnnotated(row, tab) || !SCORED.has(cell.state)) continue;
    if (cell.state === '失败') { errorTypes.failure++; continue; }
    if (tab === 'date') {
      if (row.expected_date && (cell.state === '未识别' || (cell.state === '错误' && !cell.value))) errorTypes.dateMissing++;
      else if (cell.state === '错误') {
        if (!row.expected_date && cell.value) errorTypes.blankFalseRead++;
        else if (row.expected_date && cell.value) errorTypes.dateWrong++;
        else errorTypes.other++;
      }
    } else if (cell.state === '错误') {
      if (cell.decision === '部分匹配') errorTypes.partial++;
      else if (row.expected_seal === '不匹配' && cell.decision === '匹配') errorTypes.falseAccept++;
      else if (row.expected_seal === '匹配' && cell.decision === '不匹配') errorTypes.falseReject++;
      else errorTypes.other++;
    }
  }
  summary.errorTypes = errorTypes;
  if (tab === 'date') {
    const cohort = blank => {
      const samples = rows.filter(row => rowAnnotated(row, 'date') && Boolean(row.expected_date) !== blank);
      return {samples: samples.length, ...cellSummary(samples.map(row => getCell(row, 'date', 'date')))};
    };
    summary.cohorts = {dated: cohort(false), blank: cohort(true)};
  }
  return summary;
}

function normalizedColumns(columns = [], tab = 'seal') {
  const selected = tab === 'date' ? [{key: 'date', label: '识别日期'}] : columns;
  return [...new Map(selected.map(column => {
    const item = typeof column === 'string' ? {key: column, label: column} : column;
    return [item.key, item];
  })).values()];
}

export function groupStatistics(rows = [], {by = 'company', tab = 'seal', columns = []} = {}) {
  const groups = new Map();
  for (const row of rows) {
    const key = String(row[by] ?? '').trim();
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups].map(([key, items]) => {
    const coverage = summarizeCoverage(items, tab);
    return {key, label: key || (by === 'requirement' ? '未标注签章要求' : '未分类'),
      total: coverage.total, annotated: coverage.annotated, evaluable: coverage.evaluable,
      anomalies: coverage.anomalies, coverage,
      channels: normalizedColumns(columns, tab).map(column => ({...column, ...summarizeChannel(items, column.key, tab)}))};
  }).sort((a, b) => b.anomalies - a.anomalies || b.total - a.total || a.label.localeCompare(b.label, 'zh-CN'));
}

export function compareChannels(rows = [], keys = [], tab = 'seal') {
  const columns = normalizedColumns(keys, tab);
  const shared = columns.length ? rows.filter(row => columns.every(column => SCORED.has(getCell(row, column.key, tab).state))) : [];
  return {total: shared.length, excluded: rows.length - shared.length,
    channels: columns.map(column => ({...column, ...summarizeChannel(shared, column.key, tab)}))};
}

function pairDisagreements(rows, leftKey, rightKey) {
  const result = {leftKey, rightKey, total: 0, disagreements: 0,
    onlyLeftCorrect: 0, onlyRightCorrect: 0, bothWrong: 0, bothCorrect: 0};
  for (const row of rows) {
    const left = getCell(row, leftKey), right = getCell(row, rightKey);
    if (![left, right].every(cell => ['正确', '错误'].includes(cell.state) && DECISIONS.has(cell.decision))) continue;
    result.total++;
    if (left.decision === right.decision) continue;
    result.disagreements++;
    if (left.state === '正确' && right.state === '正确') result.bothCorrect++;
    else if (left.state === '正确') result.onlyLeftCorrect++;
    else if (right.state === '正确') result.onlyRightCorrect++;
    else result.bothWrong++;
  }
  return result;
}

export function summarizeDisagreements(rows = [], columns = []) {
  const keys = columns.length ? normalizedColumns(columns).map(column => column.key)
    : [...new Set(rows.flatMap(row => Object.keys(row.seals || {})))];
  const remoteKeys = ['qingtong_template', 'qingtong_ocr'];
  return {remote: pairDisagreements(rows, ...remoteKeys),
    local: keys.filter(key => key.startsWith('local:')).flatMap(local =>
      remoteKeys.map(remote => pairDisagreements(rows, remote, local)))};
}
