export const WORK_FILTERS = {all: '全部', ready: '待开始', queued: '排队中', running: '识别中', completed: '已完成', failed: '失败', processing: '待处理'};
export function workFilterValues(filter) {
  const values = Array.isArray(filter) ? filter : filter ? [filter] : [];
  const order = ['ready', 'queued', 'running', 'completed', 'failed'];
  return [...new Set(values.flatMap(value => value === 'processing' ? ['ready', 'queued', 'running'] : value === 'all' ? [] : [value]))]
    .filter(value => order.includes(value)).sort((a, b) => order.indexOf(a) - order.indexOf(b));
}
export function overviewStatus(item, workbench) {
  if (item.status === 'awaiting_upload') return 'ready';
  if (['ready', 'queued', 'running', 'cancelled'].includes(item.status)) return item.status;
  return workbench.category(item) === 'failed' ? 'failed' : 'completed';
}
export function matchesOverview(item, filter, workbench, type = '', query = '') {
  const status = overviewStatus(item, workbench), filters = workFilterValues(filter);
  if (status === 'cancelled' || (filters.length && !filters.includes(status))) return false;
  query = query.trim().toLocaleLowerCase();
  return !query || [item.filename, item.record?.fields?.['客户订单号']].some(value => String(value || '').toLocaleLowerCase().includes(query));
}
export function overviewCounts(items, workbench) {
  const counts = {total: 0, completed: 0, ready: 0, queued: 0, running: 0, failed: 0, processed: 0};
  for (const item of items) {
    const status = overviewStatus(item, workbench);
    if (status === 'cancelled') continue;
    counts.total++; counts[status]++;
    if (['completed', 'failed'].includes(status)) counts.processed++;
  }
  return counts;
}
export function selectionKey(item) { return item.job?.id ? `job:${item.job.id}` : `record:${item.id}`; }
export function orderedQueue(items, workbench) {
  const rank = {running: 0, queued: 1, ready: 2, failed: 3, completed: 4, cancelled: 5};
  return [...items].sort((a, b) => rank[overviewStatus(a, workbench)] - rank[overviewStatus(b, workbench)]
    || (a.status === 'queued' && b.status === 'queued' ? (b.job?.priority || 0) - (a.job?.priority || 0)
      || String(a.job?.uploaded_at || '').localeCompare(b.job?.uploaded_at || '') : 0));
}
