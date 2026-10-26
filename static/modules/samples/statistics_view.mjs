import {escapeHtml as esc} from '../core/ui.mjs';
import {summarizeCoverage, summarizeChannel, groupStatistics, compareChannels, summarizeDisagreements} from './statistics_metrics.mjs';

export const statPercent = value => value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`;
export const statTime = value => String(value || '').replace('T', ' ').replace(/(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/, '') || '时间未记录';

function drill(count, filter, {channel = '', company, requirement, label, link = false} = {}) {
  if (!count) return '<span class="sample-stat-zero">0</span>';
  return `<button type="button" class="sample-stat-number${link ? ' sample-stat-drill-link' : ''}" data-stat-drill="${esc(filter)}" data-stat-channel="${esc(channel)}"${company !== undefined ? ` data-stat-company="${esc(company)}"` : ''}${requirement !== undefined ? ` data-stat-requirement="${esc(requirement)}"` : ''} title="${esc(label || '查看对应样单')}" aria-label="${esc(label || '查看对应样单')}，${count} 张">${count}${link ? ' 张 <span aria-hidden="true">›</span>' : ''}</button>`;
}

export function statisticsScopeSummary(data, rows, taskId) {
  const batch = data.batches.find(item => item.id === taskId);
  if (batch) return `指定批次 · ${batch.name || '样单测试'} · 当前范围 ${rows.length} 张`;
  const batches = new Set(rows.map(row => row.task_id).filter(Boolean));
  return `每张最新测试${batches.size ? ` · 来自 ${batches.size} 个批次` : ''}`;
}

export function statisticsScopeNote(data, rows, taskId) {
  const batch = data.batches.find(item => item.id === taskId);
  if (batch) {
    const schemes = [...(batch.scheme_labels?.date || []).map(label => `日期：${label}`),
      ...(batch.scheme_labels?.seal || []).map(label => `印章：${label}`)];
    return `仅统计「${batch.name || '样单测试'}」涉及的样单，当前公司筛选命中 ${rows.length} 张。${statTime(batch.created_at)} · ${batch.status || '状态未记录'}${schemes.length ? ` · ${schemes.join('、')}` : ''}。评估使用当前人工标注。`;
  }
  const times = rows.map(row => row.tested_at).filter(Boolean).sort((a, b) => new Date(a) - new Date(b));
  const batches = new Set(rows.map(row => row.task_id).filter(Boolean));
  return `每张样单只取最近一次测试，未测试样单也保留；可能混合不同时间与识别方案，不会为某一路单独补取旧结果。${times.length ? `测试时间 ${statTime(times[0])} 至 ${statTime(times.at(-1))}${batches.size ? `，来自 ${batches.size} 个批次` : ''}。` : ''}`;
}

export function statisticsSummary(rows, tab) {
  const c = summarizeCoverage(rows, tab);
  const labels = tab === 'seal' ? `应匹配 ${c.positive} · 应不匹配 ${c.negative}`
    : `有日期 ${c.positive} · 空白日期 ${c.negative}`;
  const statuses = [
    c.tested ? `<span>已测试 ${c.tested}</span>` : '',
    c.unannotated ? `<span>待标注 ${drill(c.unannotated, 'pending_truth', {label:'查看待标注样单'})}</span>` : '',
    c.untested ? `<span>未测试 ${drill(c.untested, 'untested', {label:'查看未测试样单'})}</span>` : '',
    c.pending ? `<span>测试中 ${c.pending}</span>` : '',
    c.failed ? `<span>运行失败 ${c.failed}</span>` : '',
  ].filter(Boolean).join('');
  return `<div class="sample-stat-summary-main">
    <div class="sample-stat-summary-item"><span>样单数</span><strong>${c.total}</strong></div>
    <div class="sample-stat-summary-item" title="${labels}；标注覆盖率 ${statPercent(c.total ? c.annotated / c.total : null)}"><span>已标注</span><strong>${c.annotated}</strong></div>
    <div class="sample-stat-summary-item" title="${tab === 'seal' ? '至少一路有可评测结果，每张只计一次' : '已有日期标注且有可评测结果'}"><span>参与评测</span><strong>${c.evaluable}</strong></div>
    <div class="sample-stat-summary-item" title="${tab === 'seal' ? '任一路错误、未识别或失败，每张只计一次' : '日期错误、未识别或失败'}"><span>异常样单</span><strong>${drill(c.anomalies, 'errors', {label:'查看异常样单', link:true})}</strong></div>
  </div>${statuses ? `<div class="sample-stat-summary-status">${statuses}</div>` : ''}`;
}

function channelTable(rows, columns, tab, distribution) {
  const body = columns.map(column => {
    const s = summarizeChannel(rows, column.key, tab);
    const excluded = ['待标注', '未运行', '未测试', '测试中'].filter(state => s.counts[state])
      .map(state => `${state} ${s.counts[state]}`).join(' · ');
    return `<tr><th scope="row" class="sample-stat-scheme-name" title="${esc(column.label)}">${esc(column.label)}${excluded ? `<small class="sample-stat-excluded">未计入：${excluded}</small>` : ''}</th>
      <td class="sample-stat-accuracy"><strong>${statPercent(s.accuracy)}</strong>${distribution(s)}</td>
      <td class="sample-stat-evaluated"><strong>${s.correct}</strong><span> / ${s.total}</span></td>
      <td>${drill(s.incorrect, 'wrong', {channel:column.key, label:`${column.label}：查看判定错误`, link:true})}</td>
      <td>${drill(s.missing, 'unrecognized', {channel:column.key, label:`${column.label}：查看未识别`, link:true})}</td>
      <td>${drill(s.failed, 'failed', {channel:column.key, label:`${column.label}：查看失败`, link:true})}</td></tr>`;
  }).join('');
  return `<section class="sample-stat-block sample-stat-schemes"><header><h3>各识别方案表现</h3></header><div class="sample-stat-matrix-wrap"><table class="sample-stat-matrix sample-stat-scheme-table"><thead><tr><th>识别方案</th><th>${tab === 'seal' ? '判定正确率' : '日期正确率'}</th><th>正确 / 可评测</th><th>错误</th><th>未识别</th><th>失败</th></tr></thead><tbody>${body || '<tr><td colspan="6" class="empty-state">当前范围暂无识别方案</td></tr>'}</tbody></table></div></section>`;
}

function analysisPanel(key, title, meta, content) {
  return `<details class="sample-stat-block sample-stat-analysis" data-stat-analysis="${key}"><summary><span class="sample-stat-analysis-title">${title}</span>${meta ? `<span class="sample-stat-analysis-meta">${meta}</span>` : ''}</summary><div class="sample-stat-analysis-body">${content}</div></details>`;
}

function errorTable(rows, columns, tab) {
  const types = tab === 'seal'
    ? [['falseAccept','误放行','false_accept'], ['falseReject','误拒绝','false_reject'], ['partial','部分匹配','partial'], ['other','其他判定错误','other'], ['failure','执行失败','failed']]
    : [['dateWrong','日期读错','wrong_date'], ['dateMissing','漏读日期','date_missed'], ['blankFalseRead','空白误读出日期','date_spurious'], ['failure','执行失败','failed']];
  const explanation = tab === 'seal' ? '误放行：应不匹配却判为匹配；误拒绝：应匹配却判为不匹配。未识别、部分匹配单独列出。'
    : '日期读错与漏读只统计标注有日期的样单；空白误读只统计标注无日期的样单。';
  const headers = types.map(([,label]) => `<th>${label}</th>`).join('');
  return `<section class="sample-stat-block"><header><h3>错误类型分布</h3><p>${explanation}点击数量查看对应样单。</p></header><div class="sample-stat-matrix-wrap"><table class="sample-stat-matrix"><thead><tr><th>识别方案</th>${headers}${tab === 'seal' ? '<th>未识别</th>' : ''}</tr></thead><tbody>${columns.map(column => {
    const s = summarizeChannel(rows, column.key, tab);
    return `<tr><th scope="row">${esc(column.label)}</th>${types.map(([key,,filter]) => `<td>${drill(s.errorTypes[key], filter, {channel:column.key})}</td>`).join('')}${tab === 'seal' ? `<td>${drill(s.missing, 'unrecognized', {channel:column.key})}</td>` : ''}</tr>`;
  }).join('')}</tbody></table></div></section>`;
}

export function problemAnnotationsPanel(rows) {
  const samples = [...new Map(rows.map((row, index) => [row.filename || index, row])).values()];
  const statuses = {unmarked:0, clear:0, issues:0}, types = new Map(), features = new Map();
  const add = (counter, values) => [...new Set(values || [])].forEach(label => counter.set(label, (counter.get(label) || 0) + 1));
  let legacy = 0;
  for (const row of samples) {
    const review = row.problem_review || {}, status = ['clear', 'issues'].includes(review.status) ? review.status : 'unmarked';
    statuses[status]++;
    if (status === 'issues') add(types, review.types);
    add(features, row.sample_features);
    if (row.legacy_problem_types?.length) legacy++;
  }
  const distribution = (title, counter) => `<h4>${title}</h4><div class="sample-stat-matrix-wrap"><table class="sample-stat-matrix"><thead><tr><th>标签</th><th>样单数</th></tr></thead><tbody>${[...counter].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN')).map(([label, count]) => `<tr><th scope="row">${esc(label)}</th><td>${count}</td></tr>`).join('') || '<tr><td colspan="2">暂无标记</td></tr>'}</tbody></table></div>`;
  return analysisPanel('human-problems', '人工问题标记', `已发现问题 ${statuses.issues} 张`,
    `<p>当前范围内整张样单的人工观察，仅关联各自本次测试；不改变自动评测结果或正确率。同一样单同一标签只计一次，多选标签合计可超过问题样单数。</p>
    <div class="sample-stat-summary-status"><span>尚未标注 ${statuses.unmarked}</span><span>已核对无问题 ${statuses.clear}</span><span>已发现问题 ${statuses.issues}</span></div>
    ${distribution('本次发现的问题', types)}${distribution('图片与版式特征（跨测试保留）', features)}
    ${legacy ? `<p>${legacy} 张样单有未关联测试的旧标记，未计入本次问题；可打开样单核对后标注。</p>` : ''}`);
}

function dateCohorts(rows) {
  const s = summarizeChannel(rows, 'date', 'date');
  return analysisPanel('date-cohorts', '有日期与空白日期分别评估', '区分真实日期与空白样单', `<p>分开看两类样单，避免大量空白日期掩盖实际日期识别效果。</p><div class="sample-stat-matrix-wrap"><table class="sample-stat-matrix"><thead><tr><th>人工标注</th><th>样单数</th><th>可评测</th><th>正确</th><th>正确率</th></tr></thead><tbody>${[['dated','有日期','date_with_value'],['blank','空白日期','date_blank']].map(([key,label,filter]) => {
    const all = rows.filter(row => key === 'dated' ? Boolean(row.expected_date) : row.expected_date === '');
    const c = s.cohorts[key];
    return `<tr><th scope="row">${label}</th><td>${drill(all.length, filter)}</td><td>${c.total}</td><td>${c.correct}</td><td>${statPercent(c.accuracy)}</td></tr>`;
  }).join('')}</tbody></table></div>`);
}

function commonComparison(rows, columns, tab) {
  const active = columns.filter(column => summarizeChannel(rows, column.key, tab).total > 0);
  if (active.length < 2) return '';
  const common = compareChannels(rows, active.map(column => column.key), tab);
  return analysisPanel('common', '共同样本对比', `${common.total} 张共同样单`, `<p>只比较以下方案均有可评测结果的同一组样单；未识别与失败也参与评测。共同运行的方案或配置仍可能来自不同时间。</p>${common.total ? `<div class="sample-stat-comparison">${common.channels.map(channel => `<div><span>${esc(active.find(column => column.key === channel.key)?.label || channel.key)}</span><strong>${statPercent(channel.accuracy)}</strong><small>正确 ${channel.correct} / 同组样单 ${common.total}</small></div>`).join('')}</div>` : '<p class="empty-state">这些方案暂时没有共同的可评测样单，请在同一批样单上运行需要对比的方案。</p>'}`);
}

function disagreementPanel(rows, columns) {
  const d = summarizeDisagreements(rows, columns);
  const pairs = [d.remote, ...d.local].filter(pair => pair && pair.total > 0);
  if (!pairs.length) return '';
  const name = key => columns.find(column => column.key === key)?.label || key;
  return analysisPanel('disagreements', '识别方案的判定分歧', `${pairs.length} 组方案对比`, `<p>只对比双方有明确判定的样单；未识别与失败不算分歧。“双方都错”包含部分匹配等判定。</p><div class="sample-stat-matrix-wrap"><table class="sample-stat-matrix"><thead><tr><th>方案 A / 方案 B</th><th>可对比样单</th><th>判定不同</th><th>仅 A 正确</th><th>仅 B 正确</th><th>双方都错</th></tr></thead><tbody>${pairs.map(pair => `<tr><th scope="row">${esc(name(pair.leftKey))}<small>${esc(name(pair.rightKey))}</small></th><td>${pair.total}</td><td>${pair.disagreements}</td><td>${pair.onlyLeftCorrect}</td><td>${pair.onlyRightCorrect}</td><td>${pair.bothWrong}</td></tr>`).join('')}</tbody></table></div><div class="sample-stat-inline-actions">${drill(d.remote.disagreements, 'remote', {label:'查看清瞳两路不一致'})}<span>张清瞳两路不一致</span><button type="button" class="text-button" data-stat-drill="local">查看清瞳与本地分歧 →</button></div>`);
}

function groupPanel(rows, {columns, tab, groupBy, showAllGroups}) {
  const groups = groupStatistics(rows, {by:groupBy, tab, columns});
  const shown = showAllGroups ? groups : groups.slice(0, 8);
  return analysisPanel('groups', '问题集中在哪里', `${groups.length} 组 · 按${groupBy === 'company' ? '公司' : '签章要求'}`, `<header class="sample-stat-block-heading"><p>按异常样单数从多到少排序，每个方案同时列出正确率与评测数量。</p><div class="sample-stat-group-controls" role="group" aria-label="分组统计方式"><button type="button" data-stat-group-by="company" aria-pressed="${groupBy === 'company'}">按公司</button>${tab === 'seal' ? `<button type="button" data-stat-group-by="requirement" aria-pressed="${groupBy === 'requirement'}">按签章要求</button>` : ''}</div></header><div class="sample-stat-matrix-wrap"><table class="sample-stat-matrix"><thead><tr><th>${groupBy === 'company' ? '客户公司' : '签章要求'}</th><th>样单</th><th>已标注</th><th>异常样单</th>${columns.map(column => `<th>${esc(column.label)}</th>`).join('')}</tr></thead><tbody>${shown.map(group => `<tr><th scope="row">${esc(group.label)}</th><td>${group.total}</td><td>${group.annotated}</td><td>${group.key ? drill(group.anomalies, 'errors', groupBy === 'company' ? {company:group.key} : {requirement:group.key}) : group.anomalies}</td>${group.channels.map(channel => `<td>${statPercent(channel.accuracy)}<small>正确 ${channel.correct} / 评测 ${channel.total}</small></td>`).join('')}</tr>`).join('') || `<tr><td colspan="${columns.length + 4}" class="empty-state">当前范围没有样单</td></tr>`}</tbody></table></div>${groups.length > 8 ? `<button type="button" class="text-button sample-stat-group-more" data-stat-show-groups>${showAllGroups ? '收起' : `显示全部 ${groups.length} 组`} ↓</button>` : ''}`);
}

export function statisticsOverview(rows, options) {
  const {tab, columns, distribution} = options;
  return channelTable(rows, columns, tab, distribution)
    + errorTable(rows, columns, tab) + problemAnnotationsPanel(rows)
    + (tab === 'date' ? dateCohorts(rows) : commonComparison(rows, columns, tab) + disagreementPanel(rows, columns))
    + groupPanel(rows, options);
}
