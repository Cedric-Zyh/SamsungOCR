import {escapeHtml} from '../core/ui.mjs';
import {elapsed} from './process_history_model.mjs';

const LABELS = {succeeded:'已完成', failed:'执行失败', cancelled:'已取消', running:'执行中', pending:'未开始', waiting:'等待上传', incomplete:'未记录完成', unknown:'未记录'};
export const executionLabel = status => ({succeeded:'处理完成', failed:'处理失败', cancelled:'处理已取消', running:'处理中', queued:'排队中', ready:'待开始', awaiting_upload:'待上传'}[status] || '流程记录');
export const formatTime = value => value ? String(value).replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '') : '时间未记录';
const shortTime = value => value ? formatTime(value).split(' ').at(-1) : '—';
const duration = value => value != null ? `${value} 秒` : '—';
const chevron = '<svg class="process-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m7 4 6 6-6 6"/></svg>';
export function statusIcon(status) {
  return `<span class="process-state-icon" data-state="${escapeHtml(status)}" aria-hidden="true">${status === 'succeeded' ? '<svg viewBox="0 0 20 20"><path d="m5 10 3.5 3.5L15 6.5"/></svg>' : status === 'failed' ? '!' : status === 'cancelled' || status === 'incomplete' ? '−' : ''}</span>`;
}

function eventLog(events) {
  return `<ol class="process-event-log">${events.map(event => `<li><time>${escapeHtml(formatTime(event.time))}</time><div><strong>${escapeHtml(event.title || '流程节点')}</strong>${event.detail ? `<p>${escapeHtml(event.detail)}</p>` : ''}</div></li>`).join('')}</ol>`;
}

function stepRow(step) {
  return `<details class="process-step"${step.status === 'failed' || step.status === 'running' ? ' open' : ''}>
    <summary class="process-step-columns"><span class="process-step-name">${chevron}<strong>${escapeHtml(step.title)}</strong>${step.channel === 'parallel' ? '<small class="process-parallel-tag">并行</small>' : ''}</span><span class="process-step-method" data-label="识别方式">${escapeHtml(step.methodLabel)}</span><span class="process-step-state" data-state="${escapeHtml(step.status)}">${statusIcon(step.status)}${LABELS[step.status] || '未记录'}</span><span class="process-step-duration" data-label="耗时">${escapeHtml(duration(step.duration))}</span></summary>
    <div class="process-step-detail"><div class="process-step-times"><span>开始 <time>${escapeHtml(formatTime(step.start))}</time></span><span>完成 <time>${escapeHtml(step.end ? formatTime(step.end) : '未记录')}</time></span></div>${step.events.length ? eventLog(step.events) : '<p class="process-muted">该步骤没有更详细的事件记录。</p>'}</div>
  </details>`;
}

function group({title, status, note, value, content, open=false}) {
  return `<details class="process-group"${open ? ' open' : ''}><summary>${chevron}${statusIcon(status)}<strong>${title}</strong><span class="process-group-note">${note}</span><span class="process-group-value">${escapeHtml(value)}</span></summary><div class="process-group-content">${content}</div></details>`;
}

export function renderProcessHistory(model) {
  if (!model.events.length) return '<div class="empty-state">暂无流程记录</div>';
  const milestones = [
    ['文件准备', model.fileStatus, LABELS[model.fileStatus]],
    ['排队等待', model.queueStatus, model.wait != null ? duration(model.wait) : LABELS[model.queueStatus]],
    ['识别处理', model.recognitionStatus, model.processing != null ? duration(model.processing) : LABELS[model.recognitionStatus]],
  ];
  const preparation = group({title:'文件准备', status:model.fileStatus, note:model.fileStatus === 'succeeded' ? '文件已提交并上传' : LABELS[model.fileStatus], value:shortTime(model.files.at(-1)?.time), content:model.files.length ? eventLog(model.files) : '<p class="process-muted">没有文件上传记录。</p>'});
  const queued = group({title:'排队等待', status:model.queueStatus, note:model.queueStatus === 'running' ? '等待后台处理' : LABELS[model.queueStatus], value:duration(model.wait), content:model.queue.length ? eventLog(model.queue) : '<p class="process-muted">没有排队事件记录。</p>'});
  const recognition = model.attempts.length ? model.attempts.map((attempt,index) => {
    const latest = index === model.attempts.length - 1;
    return group({title:`识别处理${model.attempts.length > 1 ? ` <small class="process-attempt-label">第 ${index+1} 次执行</small>` : ''}`, status:attempt.status,
      note:attempt.steps.some(step => step.channel === 'parallel') ? '部分步骤并行执行' : LABELS[attempt.status],
      value:duration(latest ? model.processing : elapsed(attempt.start, attempt.end)), open:latest,
      content:attempt.steps.length ? `<div class="process-step-header process-step-columns" aria-hidden="true"><span>处理步骤</span><span>识别方式</span><span>执行状态</span><span>耗时</span></div>${attempt.steps.map(stepRow).join('')}` : eventLog(attempt.events)});
  }).join('') : group({title:'识别处理',status:'pending',note:'等待开始',value:'—',content:'<p class="process-muted">识别开始后，处理步骤将在这里显示。</p>'});
  return `<ol class="process-milestones" aria-label="处理阶段">${milestones.map(([title,status,note]) => `<li data-state="${escapeHtml(status)}">${statusIcon(status)}<div><strong>${title}</strong><small>${escapeHtml(note)}</small></div></li>`).join('')}</ol>
    <div class="process-groups">${preparation}${queued}${recognition}</div>
    <section id="process-history-log" class="process-full-log hidden" tabindex="-1" aria-label="完整处理日志"><h3>完整处理日志 <small>${model.events.length} 条事件</small></h3>${eventLog(model.events)}</section>`;
}
