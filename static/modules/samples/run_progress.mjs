/** Follow the exact submitted run even if later runs replace sample-list rows. */
const STORAGE_KEY = 'sample-run-progress-v1';
const play = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z"/></svg>';
const spinner = '<svg class="sample-run-spinner" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 8 8"/></svg>';

export function createSampleRunProgress({environment, ui, services, currentCompany}) {
  const {$} = ui, runs = new Map(), requests = new Map();
  let presentation = {submitting: false, hasRunnable: false};
  const setText = (node, value) => { if (node.textContent !== value) node.textContent = value; };
  const current = () => runs.get(currentCompany());
  const active = () => !!current() && !current().progress?.finished;
  function save() {
    try { environment.window.localStorage?.setItem(STORAGE_KEY, JSON.stringify([...runs])); } catch {}
  }
  function render(state = presentation) {
    presentation = state;
    const run = current(), progress = run?.progress, busy = state.submitting || active();
    const button = $('#sample-run-company');
    const total = Number(progress?.total) || 0, processed = Number(progress?.processed) || 0;
    const suffix = total ? ` ${processed}/${total}` : '';
    const label = state.submitting ? '提交中…' : active() ? `运行中${suffix}` : '开始运行';
    const markup = `${busy ? spinner : play}<span>${label}</span>`;
    if (button.innerHTML !== markup) button.innerHTML = markup;
    button.disabled = busy || !state.hasRunnable;
    button.setAttribute('aria-busy', String(busy));
    const panel = $('#sample-run-progress');
    panel.hidden = !run && !state.submitting;
    if (panel.hidden) return;
    panel.dataset.state = progress?.finished ? 'complete' : 'running';
    setText($('#sample-run-progress-label'), state.submitting ? '正在提交本次测试'
      : total ? `${progress.finished ? '已结束' : '已处理'} ${processed} / ${total}` : '正在读取本次进度');
    const notes = progress ? [progress.failed && `${progress.failed} 张需处理`,
      progress.cancelled && `${progress.cancelled} 张已取消`, progress.waiting && `单证通待返回 ${progress.waiting}`,
      progress.submitting && `单证通待提交 ${progress.submitting}`, progress.running && `识别中 ${progress.running}`,
      progress.queued && `排队 ${progress.queued}`].filter(Boolean) : [];
    setText($('#sample-run-progress-note'), run?.error ? '进度连接暂时中断，正在重试'
      : notes.join(' · ') || (progress?.finished ? '本次运行完成' : '后台处理中'));
    const bar = $('#sample-run-progress-bar');
    bar.max = total || 1; bar.value = processed;
  }
  function initialize() {
    try {
      const saved = JSON.parse(environment.window.localStorage?.getItem(STORAGE_KEY) || '[]');
      if (Array.isArray(saved)) for (const row of saved) {
        if (Array.isArray(row) && typeof row[0] === 'string' && typeof row[1]?.taskId === 'string') runs.set(row[0], row[1]);
      }
    } catch {}
  }
  function start(taskId, total, company) {
    if (!taskId || !total) return;
    runs.set(company, {taskId, progress: {total, processed: 0, finished: false}});
    save(); render();
  }
  function discover(items) {
    if (current()) return;
    const candidates = items.filter(item => item.status === '测试中' && item.task_id
      && (!currentCompany() || item.company === currentCompany()));
    candidates.sort((a, b) => (b.job_created_at || '').localeCompare(a.job_created_at || ''));
    if (candidates[0]) { runs.set(currentCompany(), {taskId: candidates[0].task_id}); save(); }
  }
  function refresh() {
    const run = current();
    if (!run || run.progress?.finished || !services.samples.progress) return Promise.resolve();
    if (requests.has(run.taskId)) return requests.get(run.taskId);
    const taskId = run.taskId;
    const promise = services.samples.progress(taskId).then(progress => {
      for (const [company, saved] of runs) if (saved.taskId === taskId) runs.set(company, {taskId, progress});
      save();
    }).catch(error => {
      for (const [company, saved] of runs) if (saved.taskId === taskId) {
        if (error.status === 404) runs.delete(company);
        else saved.error = true;
      }
      save();
    }).finally(() => { if (requests.get(taskId) === promise) requests.delete(taskId); render(); });
    requests.set(taskId, promise);
    return promise;
  }
  return {initialize, render, start, discover, refresh, active};
}
