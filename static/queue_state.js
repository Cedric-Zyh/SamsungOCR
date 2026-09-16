/* Pure queue projection shared by the progress UI and regression tests. */
const ReceiptQueue = (() => {
  const labels = {awaiting_upload: '待上传', ready: '待开始', queued: '等待识别', running: '识别中',
    succeeded: '已完成', failed: '失败', cancelled: '已取消'};
  function rows(records, jobs) {
    const seen = new Set(), output = [], jobIds = new Set(jobs.map(job => job.id));
    const byId = new Map(records.map(record => [record.id, record]));
    // The API orders newest jobs first. A retry replaces the earlier display
    // of the same result while distinct uploads keep their own identities.
    for (const job of jobs) {
      const id = job.result_id || job.target_result_id;
      if (id && seen.has(id)) continue;
      if (id) seen.add(id);
      output.push({job, record: byId.get(id), id, filename: job.filename, status: job.status,
        review_status: job.status === 'succeeded' ? byId.get(id)?.review_status || job.review_status : job.review_status,
        final_result: job.status === 'succeeded' ? byId.get(id)?.final_result || job.final_result : job.final_result});
    }
    for (const record of records) {
      // A worker may commit between the status and result requests. Keep that
      // receipt represented by its job until the next status poll catches up.
      if (seen.has(record.id) || jobIds.has(record.queue?.job_id)) continue;
      seen.add(record.id);
      output.push({record, id: record.id, filename: record.filename,
        status: record.error_message || record.overall === '识别失败' ? 'failed' : 'succeeded',
        review_status: record.review_status, final_result: record.final_result || record.overall});
    }
    return output;
  }
  function counts(items) {
    const count = state => items.filter(item => item.status === state).length;
    const awaiting_upload = count('awaiting_upload'), ready = count('ready'), queued = count('queued'), running = count('running');
    return {total: items.length, completed: items.length - awaiting_upload - ready - queued - running,
      succeeded: count('succeeded'), failed: count('failed'), cancelled: count('cancelled'),
      awaiting_upload, ready, queued, running,
      pending_review: items.filter(item => ['succeeded', 'failed'].includes(item.status) && item.review_status === '待复核').length,
      status: running ? '识别中' : queued ? '等待识别' : awaiting_upload ? '等待上传' : ready ? '待开始' : items.length ? '已完成' : '暂无回单'};
  }
  return {labels, rows, counts};
})();
if (typeof module !== 'undefined') module.exports = ReceiptQueue;
