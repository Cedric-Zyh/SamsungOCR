const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
const QUEUE = new Set(['queued', 'retry_queued', 'retry_submitted', 'recovered', 'withdrawn', 'prioritized']);
const METHODS = {system:'系统', paddle_v6:'Paddle v6 Small', paddle_seal:'Paddle 印章专用', qingtong:'清瞳', danzhengtong:'单证通'};

export function seconds(value) {
  return value != null && value !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
}

export function elapsed(start, end) {
  if (!start || !end) return null;
  const value = (Date.parse(end) - Date.parse(start)) / 1000;
  return Number.isFinite(value) && value >= 0 ? Math.round(value * 100) / 100 : null;
}

function finishAttempt(attempt, event) {
  attempt.status = event.kind;
  attempt.end = event.time;
  for (const step of attempt.steps) {
    if (step.status === 'running') step.status = event.kind === 'cancelled' ? 'cancelled' : 'incomplete';
  }
}

/** Pair only one execution of the same model/channel within the same run.
 * The job state describes execution; current_status may instead be a review verdict.
 */
export function buildProcessHistory(data) {
  const events = (data.events || []).filter(event => event.kind !== 'review').map(event => ({...event}));
  // Legacy result-only history sometimes puts the business verdict in this detail.
  for (const event of events) {
    if (event.kind === 'succeeded') event.detail = event.metadata?.result_id ? `生成结果 #${event.metadata.result_id}` : '';
  }
  const job = data.job || {};
  const files = events.filter(event => ['submitted', 'uploaded'].includes(event.kind));
  const queue = events.filter(event => QUEUE.has(event.kind));
  const attempts = [];
  let attempt = null;
  const startAttempt = event => {
    attempt = {start:event.time || '', end:'', status:'running', steps:[], events:[], plan:[]};
    attempts.push(attempt);
    return attempt;
  };
  for (const event of events) {
    if (QUEUE.has(event.kind) || event.kind === 'submitted') {
      if (attempt?.status === 'running') {
        attempt.status = 'incomplete';
        attempt.steps.filter(step => step.status === 'running').forEach(step => { step.status = 'incomplete'; });
      }
      attempt = null;
      continue;
    }
    if (event.kind === 'running') {
      if (attempt?.status === 'running') finishAttempt(attempt, {kind:'incomplete', time:event.time});
      startAttempt(event).events.push(event);
      continue;
    }
    if (event.kind !== 'progress' && !TERMINAL.has(event.kind)) continue;
    if (!attempt || TERMINAL.has(attempt.status)) startAttempt(TERMINAL.has(event.kind) ? {time:''} : event);
    attempt.events.push(event);
    if (TERMINAL.has(event.kind)) { finishAttempt(attempt, event); continue; }
    const meta = event.metadata || {};
    const stage = meta.target_id || meta.stage || event.title || '识别步骤';
    const method = meta.method || meta.provider || 'system';
    const channel = meta.channel || 'main';
    const isModel = meta.provider === 'model';
    const key = `${stage}:${method}:${channel}`;
    const phase = meta.phase || (['completed','failed'].includes(meta.stage) ? meta.stage : 'started');
    if (Array.isArray(meta.steps)) attempt.plan = meta.steps;
    // Preparing emits a start marker only; a later model stage is its end boundary.
    if (isModel && stage !== 'preparing') {
      const preparation = attempt.steps.find(step => step.stage === 'preparing' && step.status === 'running');
      if (preparation) { preparation.status = 'succeeded'; preparation.end = event.time; }
    }
    let step = [...attempt.steps].reverse().find(item => item.key === key && item.status === 'running');
    if (!step) {
      step = {key, stage, method, channel, title:meta.target || String(event.title || '识别步骤').replace(/\s*·\s*(开始|完成|失败)$/, ''),
        methodLabel:meta.method_label || METHODS[method] || method, status:'running', start:'', end:'', duration:null, events:[]};
      attempt.steps.push(step);
    }
    step.events.push(event);
    if (phase === 'completed' || phase === 'failed') {
      step.status = phase === 'completed' ? 'succeeded' : 'failed';
      step.end = event.time;
      step.duration = seconds(meta.elapsed_seconds) ?? elapsed(step.start, step.end);
    } else if (!step.start) step.start = meta.started_at || event.time;
  }
  const last = attempts.at(-1);
  const currentRun = last && !!job.started_at;
  if (last) {
    // Use planned steps to make work not yet reached visible, without claiming success.
    const plan = currentRun && job.progress?.steps?.length ? job.progress.steps : last.plan;
    for (const planned of plan) {
      if (last.steps.some(step => step.stage === planned.target_id && step.method === planned.method)) continue;
      last.steps.push({key:planned.key, stage:planned.target_id, method:planned.method, channel:'main',
        title:planned.target || planned.target_id, methodLabel:planned.method_label || METHODS[planned.method] || planned.method,
        status:planned.phase === 'completed' ? 'succeeded' : planned.phase === 'failed' ? 'failed' : planned.phase === 'started' ? 'running' : 'pending',
        start:planned.started_at || '', end:'', duration:seconds(planned.elapsed_seconds), events:[]});
    }
    if (currentRun) {
      if (job.status === 'running') last.status = 'running';
      else if (TERMINAL.has(job.status) && !TERMINAL.has(last.status)) finishAttempt(last, {kind:job.status, time:job.finished_at});
      last.start = job.started_at;
      if (job.finished_at) last.end = job.finished_at;
    }
    for (const step of last.steps) {
      if (step.status !== 'running' || !currentRun) continue;
      const current = [job.progress, job.progress?.parallel].find(progress => progress &&
        (progress.target_id || progress.stage) === step.stage && progress.method === step.method && (progress.channel || 'main') === step.channel);
      if (current) step.duration = seconds(current.elapsed_seconds);
    }
  }
  const status = job.status || last?.status || (events.at(-1)?.status === 'queued' ? 'queued' : 'unknown');
  const processing = last ? (currentRun ? seconds(job.processing_seconds) : null) ?? elapsed(last.start, last.end) : null;
  const wait = job.uploaded_at ? seconds(job.wait_seconds) : null;
  const fileStatus = files.some(event => event.kind === 'uploaded') || job.uploaded_at ? 'succeeded' : files.length ? 'waiting' : 'unknown';
  const queueStatus = ['awaiting_upload','ready'].includes(status) ? 'pending' : status === 'queued' ? 'running' : (queue.length || (job.uploaded_at && job.started_at)) && last?.start ? 'succeeded' : status === 'cancelled' && queue.length ? 'cancelled' : 'unknown';
  return {events, files, queue, attempts, status, processing, wait, fileStatus, queueStatus,
    recognitionStatus:!last ? 'pending' : ['awaiting_upload','ready','queued'].includes(status) ? 'pending' : last.status,
    parallel:!!last?.steps.some(step => step.channel === 'parallel')};
}
