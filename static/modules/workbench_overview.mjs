export const WORK_FILTERS = {all: '全部', ready: '待开始', running: '处理中', processing: '待开始 / 处理中', review: '待复核', passed: '通过', rejected: '不通过', failed: '失败'};
export const DOCUMENT_TYPES = {receipt: '三星出库回单', product_continuation: '商品明细续页', warehouse_authorization: '仓库货物接收委托书', unknown: '未知文档', unclassified: '待分类'};
const pending = new Set(['awaiting_upload', 'ready', 'queued']);
export function overviewStatus(item, workbench) {
  return pending.has(item.status) ? 'ready' : item.status === 'running' ? 'running' : workbench.category(item);
}
export function overviewType(item) {
  const type = item.record?.document_type?.type;
  if (Object.hasOwn(DOCUMENT_TYPES, type)) return type;
  return pending.has(item.status) || item.status === 'running' ? 'unclassified' : 'unknown';
}
export function overviewCustomer(item) {
  return String(item.record?.fields?.['客户名称'] || item.record?.internal_fields?.['客户名称'] || '').trim() || '待识别客户';
}
export function matchesOverview(item, filter, workbench, type = '', query = '') {
  const status = overviewStatus(item, workbench);
  if (status === 'cancelled') return false;
  if (filter !== 'all' && !(filter === 'processing' ? ['ready', 'running'].includes(status) : status === filter)) return false;
  if (type && overviewCustomer(item) !== type) return false;
  query = query.trim().toLocaleLowerCase();
  return !query || [item.filename, item.record?.fields?.['客户订单号']].some(value => String(value || '').toLocaleLowerCase().includes(query));
}
export function overviewCounts(items, workbench) {
  const counts = {total: 0, completed: 0, ready: 0, running: 0, review: 0, passed: 0, rejected: 0, failed: 0};
  for (const item of items) {
    const status = overviewStatus(item, workbench);
    if (status === 'cancelled') continue;
    counts.total++; counts[status]++;
    if (!['ready', 'running'].includes(status)) counts.completed++;
  }
  return counts;
}
export function selectionKey(item) { return item.job?.id ? `job:${item.job.id}` : `record:${item.id}`; }
export function deletionTarget(item) {
  if (item.job?.id && (pending.has(item.status) || item.status === 'running')) return {kind: 'job', id: item.job.id};
  if (Number.isSafeInteger(Number(item.id)) && Number(item.id) > 0) return {kind: 'record', id: Number(item.id)};
  return null;
}
