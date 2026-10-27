import {escapeHtml, filenameParts} from '../core/ui.mjs';

const STAGES = {routing:'整页文字与版式', fields:'印刷字段', products:'商品明细',
  handwriting:'手写签名', date:'签收日期', seal:'客户印章', other:'其他识别'};
const STATUS = {completed:'已完成', succeeded:'已完成', failed:'处理失败', running:'处理中', incomplete:'处理中断', interrupted:'处理中断', unknown:'未记录'};
const ORDER = ['routing', 'fields', 'products', 'handwriting', 'date', 'seal', 'other'];
const chevron = '<svg class="review-call-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m7 4 6 6-6 6"/></svg>';
const validSeconds = value => value !== null && value !== undefined && value !== '' && typeof value !== 'boolean'
  && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
export const runDuration = value => {
  const number = validSeconds(value);
  return number == null ? '未记录' : number > 0 && number < .01 ? '< 0.01 秒' : `${Number(number.toFixed(2))} 秒`;
};
export const runTime = value => {
  if (!value) return '未记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '未记录';
  const pad = number => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};
const count = value => Math.max(0, Math.floor(Number(value) || 0));
const inputName = call => filenameParts(String(call.input_name || '').replaceAll('\\', '/')).name || '输入图片未记录';

export function runTiming(data, context) {
  const total = validSeconds(data.processing_seconds);
  let initialization = validSeconds(data.model_initialization_seconds);
  // Static assets can be refreshed before the backend restarts. A legacy
  // trace may use the open receipt's timings only when they describe this run.
  if (initialization == null && context && ['completed','succeeded'].includes(data.status)
      && Number(data.result_id) > 0 && Number(data.result_id) === Number(context.id || context.result_id)) {
    const start = Date.parse(data.started_at), end = Date.parse(data.finished_at);
    const queued = Date.parse(context.queue?.started_at), updated = Date.parse(context.updated_at);
    const sameRun = Number.isFinite(queued) ? Math.abs(queued - start) <= 1100
      : Number.isFinite(updated) && updated >= start - 1000 && updated <= end + 1000;
    const recordedTotal = validSeconds(context.processing_seconds);
    const steps = context.processing_timings?.steps;
    if (sameRun && Number.isFinite(start) && Number.isFinite(end) && end >= start
        && total != null && recordedTotal != null && Math.abs(total - recordedTotal) < .1
        && steps && typeof steps === 'object' && !Array.isArray(steps)) {
      const values = Object.entries(steps)
        .filter(([name]) => name.endsWith('_model_setup') || name === 'paddle_text_box_setup')
        .map(([,step]) => validSeconds(step?.seconds));
      if (values.length && values.every(value => value != null)) initialization = values.reduce((sum,value) => sum + value, 0);
    }
  }
  if (total == null || initialization == null || initialization > total + .01) return {initialization:null, recognition:null};
  initialization = Math.min(total, initialization);
  return {initialization, recognition:Math.max(0, total - initialization)};
}

export function callImageUrl(value) {
  if (typeof value !== 'string' || /[\\\x00-\x20]/.test(value)
      || !/^\/files\/(?:samples\/)?(?:artifacts|previews)\//.test(value)) return '';
  try {
    const path = decodeURIComponent(value.split(/[?#]/, 1)[0]);
    if (/[\\\x00-\x1f\x7f]/.test(path) || path.split('/').some(part => part === '..' || part === '.')) return '';
  } catch { return ''; }
  return value;
}

function callRow(call, index, imageKeys) {
  const id = String(call.id ?? index + 1), failed = call.status === 'failed';
  const imageUrl = callImageUrl(call.image_url);
  const dimensions = Number(call.width) > 0 && Number(call.height) > 0 ? `${count(call.width)} × ${count(call.height)}` : '';
  const label = inputName(call);
  const state = failed ? '失败' : call.status === 'running' ? '识别中' : call.status === 'succeeded' ? '成功' : '未记录';
  return `<tr class="review-call-row" data-state="${escapeHtml(call.status || 'unknown')}"><td><span class="review-call-index">#${escapeHtml(id)}</span><strong class="review-call-input">${escapeHtml(label)}</strong><span class="review-call-action">${escapeHtml([call.action, dimensions].filter(Boolean).join(' · '))}</span>
    ${imageUrl ? `<details class="review-call-image" data-call-image="${escapeHtml(id)}"${imageKeys.has(id) ? ' open' : ''}><summary>查看输入图</summary><img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(label)}" loading="lazy"></details>` : ''}
    ${failed ? `<p class="review-call-error">${escapeHtml(call.error || '本次识别调用失败')}</p>` : ''}</td>
    <td><span class="review-call-method">${escapeHtml(call.method_label || call.method || '方式未记录')}</span><span class="review-call-state" data-state="${escapeHtml(call.status || 'unknown')}">${state}</span></td>
    <td class="review-call-seconds">${escapeHtml(runDuration(call.seconds))}</td></tr>`;
}

export function renderRunHistory(data, {openKeys, imageKeys = new Set(), explanationOpen = false, loading = false, error = '', timingContext} = {}) {
  const available = data.available === true;
  const summary = data.summary || {}, calls = Array.isArray(data.calls) ? data.calls : [];
  const groups = new Map();
  calls.forEach((call, index) => {
    const key = call.stage || 'other';
    if (!groups.has(key)) groups.set(key, {key, label:STAGES[key] || call.stage_label || '其他识别', calls:[]});
    groups.get(key).calls.push({call,index});
  });
  const sorted = [...groups.values()].sort((a,b) => {
    const rank = key => ORDER.includes(key) ? ORDER.indexOf(key) : ORDER.length;
    return rank(a.key) - rank(b.key);
  });
  const selected = openKeys || new Set([groups.has('seal') ? 'seal' : sorted[0]?.key]);
  const displayCount = name => available ? count(summary[name]) : '—';
  const status = STATUS[data.status] || (available ? '已记录' : '未记录');
  const heading = `<div class="review-runs-summary"><div class="review-runs-stat"><span>实际调用</span><strong>${displayCount('total')}</strong><span>次</span></div><div class="review-runs-stat"><span>成功</span><strong>${displayCount('succeeded')}</strong><span>次</span></div><div class="review-runs-stat" data-state="${available && count(summary.failed) ? 'failed' : 'zero'}"><span>失败</span><strong>${displayCount('failed')}</strong><span>次</span></div></div>
    <div class="review-runs-toolbar"><div class="review-runs-toolbar-label"><span>仅最近一次处理</span><span class="review-calls-status" data-state="${escapeHtml(data.status || 'unknown')}">${status}</span></div><button type="button" class="text-button" data-runs-refresh${loading ? ' disabled' : ''}>${loading ? '正在读取…' : '刷新'}</button></div>
    ${error ? `<p class="review-call-error" role="alert">${escapeHtml(error)}</p>` : ''}`;
  if (!available) return `${heading}<div class="review-calls-empty" role="status"><strong>本次识别调用明细未记录</strong><p>${escapeHtml(data.note || '此记录尚未采集逐次识别信息，重新识别后可查看。')}</p></div>`;
  const timing = runTiming(data, timingContext);
  return `${heading}<div class="review-call-times"><span>开始 <time>${escapeHtml(runTime(data.started_at))}</time></span><span title="不含模型初始化">处理用时 <strong>${escapeHtml(runDuration(timing.recognition))}</strong></span></div>
    <div class="review-model-initialization"><span>模型初始化</span><strong>${escapeHtml(runDuration(timing.initialization))}</strong><small>${timing.initialization == null ? '此记录未单独采集，重新识别后可查看' : '单独计时，不计入处理用时'}</small></div>
    ${data.note ? `<p class="review-call-note">${escapeHtml(data.note)}</p>` : ''}
    <details class="review-call-explanation" data-call-explanation="count"${explanationOpen ? ' open' : ''}><summary>计数说明</summary>
      ${count(summary.cache_hits) ? `<p class="review-call-note">复用已有识别结果 ${count(summary.cache_hits)} 次，未计入实际调用。</p>` : ''}
      <p class="review-call-note">每次图片识别计 1 次，复用已有结果不重复计数。并行识别时，各次耗时相加可能超过处理用时。</p></details>
    ${sorted.length ? `<div class="review-call-groups">${sorted.map(group => {
      const timings = group.calls.map(({call}) => validSeconds(call.seconds));
      const completeTiming = timings.every(value => value != null);
      const total = completeTiming ? timings.reduce((sum,value) => sum + value, 0) : null;
      const failures = group.calls.filter(({call}) => call.status === 'failed').length;
      return `<details class="review-call-group" data-call-group="${escapeHtml(group.key)}"${selected.has(group.key) ? ' open' : ''}><summary>${chevron}<strong>${escapeHtml(group.label)}</strong><span class="review-call-group-count">${group.calls.length} 次${failures ? ` · ${failures} 次失败` : ''}</span><span class="review-call-group-time" title="调用耗时合计">${escapeHtml(runDuration(total))}</span></summary><div class="review-call-table-wrap"><table class="review-call-table"><thead><tr><th>输入图片 / 识别内容</th><th>识别方式</th><th>耗时</th></tr></thead><tbody>${group.calls.map(({call,index}) => callRow(call,index,imageKeys)).join('')}</tbody></table></div></details>`;
    }).join('')}</div>` : '<div class="review-calls-empty">最近一次处理没有发生实际识别调用。</div>'}`;
}
