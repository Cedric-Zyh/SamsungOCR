const filterNames = new Set(['filename', 'order_id', 'customer', 'date', 'import_date', 'overall',
  'review_status', 'text_match', 'search_prefix', 'search', 'date_status', 'seal_status', 'task_id', 'ocr_backend']);

/** Snapshot a scope so subsequent edits to the records filters cannot change it. */
export function normalizeReviewScope(scope) {
  if (!scope || !['filtered', 'selected', 'day'].includes(scope.kind)) throw Error('复核范围无效，请重新选择');
  const filters = Object.fromEntries(Object.entries(scope.filters || {})
    .filter(([key, value]) => filterNames.has(key) && value != null && String(value) !== '')
    .map(([key, value]) => [key, String(value)]).sort(([a], [b]) => a.localeCompare(b)));
  const normalized = {kind: scope.kind, label: String(scope.label || ''), filters};
  if (scope.kind === 'selected') {
    if (!Array.isArray(scope.ids) || scope.ids.some(id => !Number.isSafeInteger(id) || id <= 0)) {
      throw Error('选中的回单编号无效，请重新选择');
    }
    normalized.ids = [...new Set(scope.ids)].sort((a, b) => a - b);
  }
  if (!normalized.label) normalized.label = scope.kind === 'selected' ? `选中回单（${normalized.ids.length} 张）`
    : scope.kind === 'filtered' ? '当前筛选' : filters.import_date ? `导入日期：${filters.import_date}` : '全部日期';
  return normalized;
}

export function reviewScopeKey(scope) {
  return JSON.stringify([scope.kind, scope.filters, scope.ids || []]);
}

export function reviewScopeRequest(scope, page, URLSearchParams, deferredIds = []) {
  const params = new URLSearchParams(scope.filters);
  params.set('reviewable', '1'); params.set('page', String(page)); params.set('page_size', '100');
  if (scope.kind === 'selected') params.set('ids', scope.ids.join(','));
  params.set('deferred_ids', [...deferredIds].join(','));
  const daily = scope.kind === 'day' && scope.filters.import_date
    && Object.keys(scope.filters).every(key => key === 'import_date');
  if (daily) params.set('include_queue', '1');
  return `${daily ? '/api/daily-results' : '/api/results'}?${params}`;
}
