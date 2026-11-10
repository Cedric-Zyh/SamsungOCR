import {escapeHtml} from '../core/ui.mjs';

const seconds = value => typeof value !== 'boolean' && value !== null && value !== undefined && value !== ''
  && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
const timestamp = value => typeof value === 'string' && value ? Date.parse(value) : NaN;
const duration = value => seconds(value) == null ? '未记录' : seconds(value) > 0 && seconds(value) < .01
  ? '< 0.01 秒' : `${Number(seconds(value).toFixed(2))} 秒`;
const clock = value => Number.isFinite(timestamp(value)) ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '未记录';
const STATES = {succeeded:'成功',completed:'已完成',received:'结果已取回',applied:'已完成',
  failed:'失败',running:'进行中',waiting:'等待结果',unknown:'未记录',interrupted:'已中断',cancelled:'已停止'};
const LANES = {local_page:'本地整页',date:'日期',seal:'印章',qingtong:'清瞳',
  danzhengtong_page:'单证通整页',danzhengtong_seal:'单证通印章',danzhengtong:'单证通（未分类）',
  image:'图像处理',initialization:'模型初始化',finalizing:'结果整合'};
const ORDER = Object.keys(LANES);
const laneKey = lane => ['routing','fields','products','handwriting','page'].includes(lane) ? 'local_page'
  : lane === 'image_processing' ? 'image' : lane === 'model_initialization' ? 'initialization' : lane || 'other';
const key = (run, item) => JSON.stringify([String(run), String(item)]);
const interval = span => {
  const start = timestamp(span.started_at), end = timestamp(span.finished_at);
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? {start,end} : null;
};

function remoteLane(call) {
  if (call.request_scope === 'page') return 'danzhengtong_page';
  if (call.request_scope === 'seal') return 'danzhengtong_seal';
  const action = typeof call.action === 'string' ? call.action : '', page = action.includes('整页'), seal = action.includes('印章');
  return page && !seal ? 'danzhengtong_page' : seal && !page ? 'danzhengtong_seal' : 'danzhengtong';
}

function recordedCallLanes(data, run) {
  const lanes = new Map();
  for (const collection of [data.calls, data.transport_calls]) {
    for (const call of Array.isArray(collection) ? collection : []) {
      if (!call || call.method !== 'danzhengtong' || call.id == null) continue;
      if (run.kind === 'retained') {
        const provenance = [call.run_id,call._run?.id,call._run?.run_id].filter(value => value != null).map(String);
        if (!provenance.includes(String(run.id))) continue;
      } else if (call._run?.retained === true) continue;
      const identity = String(call.id);
      if (!lanes.has(identity)) lanes.set(identity, new Set());
      lanes.get(identity).add(remoteLane(call));
    }
  }
  // Reused historical call IDs with different meanings are not a safe join.
  return new Map([...lanes].filter(([,values]) => values.size === 1).map(([id,values]) => [id,[...values][0]]));
}

function normalizeRun(run, index, callLanes) {
  const spans = (Array.isArray(run.spans) ? run.spans : []).filter(span => span && typeof span === 'object')
    .map((span,index) => ({...span,id:String(span.id ?? index)}));
  const byId = new Map(spans.map(span => [span.id,span]));
  function resolvedLane(span, visited = new Set()) {
    const lane = laneKey(span.lane);
    if (lane !== 'danzhengtong') return lane;
    const explicit = remoteLane(span);
    if (explicit !== 'danzhengtong') return explicit;
    const recorded = span.call_id == null ? null : callLanes.get(String(span.call_id));
    if (recorded && recorded !== 'danzhengtong') return recorded;
    const parent = byId.get(String(span.parent_id));
    if (parent && !visited.has(parent.id)) {
      visited.add(span.id);
      return resolvedLane(parent,visited);
    }
    return lane;
  }
  return {...run,id:String(run.id ?? index),kind:run.kind === 'retained' ? 'retained' : 'current',
    spans:spans.map(span => ({...span,lane:resolvedLane(span)}))};
}

function fallbackRuns(data) {
  const calls = [...(Array.isArray(data.calls) ? data.calls : []),
    ...(Array.isArray(data.transport_calls) ? data.transport_calls : [])];
  const ordinary = [], remote = [];
  calls.forEach((call,index) => {
    if (!call || typeof call !== 'object') return;
    if (call.method === 'danzhengtong') {
      const transport = call.transport || {};
      const lane = remoteLane(call);
      // A logical async call's seconds are HTTP sums, not its position on a clock.
      if (interval(transport)) remote.push({id:`remote-${index}`,label:`${LANES[lane]}已记录历时`,lane,
        started_at:transport.started_at,finished_at:transport.finished_at,seconds:transport.elapsed_seconds,
        status:call.status,kind:'lifecycle',approximate:true,call_id:call.id});
      else if (interval(call)) remote.push({id:`remote-call-${index}`,
        label:[call.action || LANES[lane],lane === 'danzhengtong_seal' ? call.input_name : ''].filter(Boolean).join(' · '),lane,
        started_at:call.started_at,finished_at:call.finished_at,
        seconds:(timestamp(call.finished_at)-timestamp(call.started_at))/1000,
        status:call.status,kind:'recognition',approximate:false,call_id:call.id});
      return;
    }
    const start = timestamp(call.started_at), measured = seconds(call.seconds);
    if (!Number.isFinite(start) || measured == null) return;
    ordinary.push({id:`call-${index}`,call_id:call.id,label:call.action || call.method_label || '识别调用',
      lane:call.method === 'qingtong' ? 'qingtong' : laneKey(call.stage),lane_label:call.stage_label,
      started_at:call.started_at,finished_at:new Date(start + measured * 1000).toISOString(),seconds:measured,
      status:call.status,kind:'recognition',approximate:true});
  });
  const runs = [];
  if (ordinary.length) runs.push({id:'legacy-local',kind:data.incremental ? 'retained' : 'current',
    label:data.incremental ? '此前保留的识别' : '已记录的本次识别',spans:ordinary,approximate:true,
    elapsed_seconds:null,note:'依据已保存的调用开始时间和实测耗时定位；未记录的环节不作推算。'});
  if (remote.length) runs.push({id:'legacy-remote',kind:'current',label:'单证通已记录历时',spans:remote,
    elapsed_seconds:null,approximate:true,note:'未记录逐次请求边界，不推算上传、提交、查询及等待区间。'});
  return runs;
}

export function hasAsyncTiming(data) {
  return data.incremental === true || [data.calls,data.transport_calls,data.remote_timings]
    .some(rows => Array.isArray(rows) && rows.some(row => row?.method === 'danzhengtong'));
}

export function resolveRunTimeline(data) {
  const recorded = data.timeline && data.timeline.version === 1 && Array.isArray(data.timeline.runs);
  const timeline = recorded ? data.timeline : {runs:fallbackRuns(data),elapsed_seconds:null,
    note:'历史记录缺少完整流程区间，仅显示能定位的已知调用。'};
  return {...timeline,recorded,runs:timeline.runs.map((run,index) => normalizeRun(run,index,recordedCallLanes(data,run)))};
}

export function timelineSummary(data) {
  const timeline = resolveRunTimeline(data);
  return {seconds:timeline.recorded ? seconds(timeline.elapsed_seconds)
    : hasAsyncTiming(data) ? null : seconds(data.processing_seconds),
    label:timeline.recorded ? timeline.elapsed_label || '全流程历时' : '全流程历时',
    approximate:timeline.recorded && timeline.approximate === true,
    started_at:timeline.recorded ? timeline.started_at : hasAsyncTiming(data) ? null : data.started_at};
}

function bounds(run) {
  const spans = run.spans.map(interval).filter(Boolean);
  if (!spans.length) return null;
  const starts = spans.map(value => value.start), ends = spans.map(value => value.end);
  if (Number.isFinite(timestamp(run.started_at))) starts.push(timestamp(run.started_at));
  if (Number.isFinite(timestamp(run.finished_at))) ends.push(timestamp(run.finished_at));
  const start = Math.min(...starts), end = Math.max(...ends);
  return {start,end,range:Math.max(1,end-start)};
}

function spanButton(span, run, axis, selected, occurrence) {
  const times = interval(span);
  if (!times) return '';
  const id = key(run.id,span.id), left = (times.start-axis.start)/axis.range*100;
  const width = (times.end-times.start)/axis.range*100;
  const known = seconds(span.seconds) ?? (times.end-times.start)/1000;
  const label = `${span.label || '已记录区间'} · ${duration(known)}${span.ongoing ? ' · 进行中' : ''}`;
  const kind = ['recognition','request','lifecycle','wait','image','initialization','finalizing'].includes(span.kind) ? span.kind : 'recognition';
  return `<button type="button" class="run-timeline-span" data-timeline-span="${escapeHtml(id)}" data-timeline-occurrence="${occurrence}" data-kind="${kind}" data-state="${escapeHtml(span.status || 'unknown')}"${span.ongoing ? ' data-ongoing="true"' : ''}${span.approximate ? ' data-approximate="true"' : ''} style="left:${left.toFixed(5)}%;width:${width.toFixed(5)}%" aria-label="${escapeHtml(label)}" aria-pressed="${selected === id}" title="${escapeHtml(label)}"><span class="sr-only">${escapeHtml(label)}</span></button>`;
}

function selectedDetails(run, selected) {
  const span = run.spans.find(value => key(run.id,value.id) === selected);
  if (!span) return '<p class="run-timeline-help">点击区间查看明细；展开行查看每次调用。</p>';
  const times = interval(span);
  const measured = seconds(span.seconds) ?? (times ? (times.end-times.start)/1000 : null);
  return `<div class="run-timeline-selection" role="status" aria-live="polite"><strong>${escapeHtml(span.label || '已记录区间')}</strong><span>${escapeHtml(STATES[span.status] || '未记录')}${span.ongoing ? ' · 记录仍在进行' : ''}</span><dl><div><dt>开始</dt><dd>${escapeHtml(clock(span.started_at))}</dd></div><div><dt>${span.ongoing ? '记录截至' : '结束'}</dt><dd>${escapeHtml(clock(span.finished_at))}</dd></div><div><dt>${span.kind === 'lifecycle' || span.kind === 'wait' ? '区间历时' : '耗时'}</dt><dd>${span.approximate && measured != null ? '约 ' : ''}${escapeHtml(duration(measured))}</dd></div>${span.call_id != null ? `<div><dt>识别调用</dt><dd>#${escapeHtml(span.call_id)}</dd></div>` : ''}</dl></div>`;
}

function renderRun(run, selected, expanded) {
  const axis = bounds(run), lanes = new Map();
  for (const span of run.spans) {
    if (!interval(span)) continue;
    if (!lanes.has(span.lane)) lanes.set(span.lane,{id:span.lane,label:LANES[span.lane] || span.lane_label || '其他环节',spans:[]});
    lanes.get(span.lane).spans.push(span);
  }
  const sorted = [...lanes.values()].sort((a,b) => (ORDER.indexOf(a.id)<0 ? 99 : ORDER.indexOf(a.id)) - (ORDER.indexOf(b.id)<0 ? 99 : ORDER.indexOf(b.id)));
  const elapsed = seconds(run.elapsed_seconds), label = run.kind === 'retained' ? '此前保留的识别' : run.label || '本次处理';
  return `<article class="run-timeline-run" data-timeline-run="${escapeHtml(run.id)}" data-run-kind="${run.kind}"><header><div><h4>${escapeHtml(label)}</h4>${run.kind === 'retained' ? '<p>单独计时，不参与本次全流程历时。</p>' : ''}</div><span>${escapeHtml(run.elapsed_label || (run.complete === false ? '已记录历时' : '流程历时'))} <strong>${run.approximate && elapsed != null ? '约 ' : ''}${escapeHtml(duration(elapsed))}</strong></span></header>
    ${axis ? `<div class="run-timeline-chart-wrap"><div class="run-timeline-chart"><div class="run-timeline-axis"><span class="run-timeline-label">环节 / 同一时间轴</span><div class="run-timeline-ticks">${[0,.25,.5,.75,1].map(ratio => `<span style="left:${ratio*100}%">${escapeHtml(duration(axis.range/1000*ratio))}</span>`).join('')}</div></div>${sorted.map(lane => {
      const laneId = key(run.id,lane.id);
      const spans = [...lane.spans].sort((a,b) => (a.kind === 'lifecycle' ? -1 : 0) - (b.kind === 'lifecycle' ? -1 : 0));
      return `<details class="run-timeline-lane" data-timeline-lane="${escapeHtml(laneId)}"${expanded.has(laneId) ? ' open' : ''}><summary><span class="run-timeline-label"><span class="run-timeline-arrow" aria-hidden="true">›</span>${escapeHtml(lane.label)}<small>${lane.spans.length}</small></span><span class="run-timeline-track">${spans.map(span => spanButton(span,run,axis,selected,'summary')).join('')}</span></summary><div class="run-timeline-call-rows">${lane.spans.map(span => `<div class="run-timeline-call"><span class="run-timeline-label" title="${escapeHtml(span.label || '已记录区间')}">${escapeHtml(span.label || '已记录区间')}</span><span class="run-timeline-track">${spanButton(span,run,axis,selected,'detail')}</span></div>`).join('')}</div></details>`;
    }).join('')}</div></div>` : '<p class="run-timeline-empty">未保存可定位的起止时间，无法绘制时间轴。</p>'}
    ${run.note ? `<p class="run-timeline-note">${escapeHtml(run.note)}</p>` : ''}${selectedDetails(run,selected)}</article>`;
}

export function renderRunTimeline(data, {selectedSpan='',expandedLanes=new Set()} = {}) {
  const timeline = resolveRunTimeline(data);
  const runs = [...timeline.runs].sort((a,b) => (a.kind === 'retained') - (b.kind === 'retained'));
  return `<section class="run-timeline" aria-label="并行处理时间轴"><div class="run-timeline-heading"><h3>并行时间轴</h3><p>实色为识别或请求，淡色为已记录历时或等待；同一流程共用时间轴。</p></div>${runs.length ? runs.map(run => renderRun(run,selectedSpan,expandedLanes)).join('') : '<p class="run-timeline-empty">尚未记录可绘制的流程区间。已有调用明细仍可在下方查看。</p>'}${timeline.note && !runs.some(run => run.note === timeline.note) ? `<p class="run-timeline-note">${escapeHtml(timeline.note)}</p>` : ''}</section>`;
}
