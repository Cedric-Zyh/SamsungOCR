/** Human-readable filter summaries also describe the continuous-review scope. */
export const filterLabels = {
  search: '搜索', search_prefix: '文件名 / 订单号开头', filename: '文件名',
  order_id: '订单号', customer: '客户', date: '签收日期', date_status: '日期核验',
  seal_status: '客户印章', overall: '核验结论', review_status: '人工复核',
  task_id: '批次', ocr_backend: '识别方式',
};

export function activeFilterEntries(filters) {
  return Object.entries(filters).filter(([key, value]) => filterLabels[key] && String(value).trim())
    .map(([key, value]) => ({key, value: String(value), label: filterLabels[key]}));
}

export function recordReviewScope(filters, kind = 'filtered', selected = []) {
  const day = filters.import_date || '';
  if (kind === 'selected') {
    const ids = [...new Set(selected.map(Number).filter(id => Number.isSafeInteger(id) && id > 0))];
    return {kind, ids, filters: {}, label: `选中 ${ids.length} 张回单`};
  }
  if (kind === 'day') return {kind, filters: {import_date: day}, label: day ? `${day} · 当天全部` : '全部日期'};
  const entries = activeFilterEntries(filters);
  const summary = entries.map(({label, value}) => `${label}：${value}`).join(' · ');
  return {kind: 'filtered', filters: {...filters}, label: [day, summary || '当前筛选'].filter(Boolean).join(' · ')};
}
