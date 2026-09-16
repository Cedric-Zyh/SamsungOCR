import {percent, escapeHtml} from './ui.mjs';

const count = value => Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
const validRate = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const metricValue = (value, total) => count(total) > 0 && validRate(value) ? percent(value) : '—';
const sampleNote = (correct, total, unit) => count(total) > 0
  ? `正确 ${count(correct)} / ${count(total)} ${unit}` : '暂无可评测样本';
const emptyState = (title, note = '') => `<div class="empty-state report-empty"><strong>${escapeHtml(title)}</strong>${note ? `<p>${escapeHtml(note)}</p>` : ''}</div>`;

function accuracyRows(items, name, emptyTitle) {
  if (!items.length) return emptyState(emptyTitle, '保存相应的评测真值，并完成样本识别后显示。');
  return [...items].sort((a, b) => {
    const aRate = count(a.total) > 0 && validRate(a.accuracy) ? a.accuracy : 2;
    const bRate = count(b.total) > 0 && validRate(b.accuracy) ? b.accuracy : 2;
    return aRate - bRate;
  }).map(item => {
    const value = metricValue(item.accuracy, item.total);
    const width = value === '—' ? 0 : item.accuracy * 100;
    return `<div class="report-accuracy-row"><span><span>${escapeHtml(item[name])}</span><small>${sampleNote(item.correct, item.total, '项')}</small></span><i aria-hidden="true"><em style="width:${width}%"></em></i><b>${value}</b></div>`;
  }).join('');
}

function errorRows(items, totalResults) {
  const errors = items.filter(item => count(item.count) > 0).sort((a, b) => count(b.count) - count(a.count));
  if (!errors.length) return emptyState(totalResults ? '尚无错误标记' : '暂无回单记录', totalResults ? '此处统计错误标记，不代表所有回单均已确认通过。' : '导入并识别回单后显示错误分布。');
  const maximum = count(errors[0].count);
  return `<p class="report-card-note">按错误标记次数统计，同一回单可能计入多项。</p>${errors.map(item => `<div class="report-error-row"><span>${escapeHtml(item.type || '未分类')}</span><b>${count(item.count)} 次</b><i aria-hidden="true"><em style="width:${count(item.count) / maximum * 100}%"></em></i></div>`).join('')}`;
}

function backendRows(items) {
  if (!items.length) return emptyState('暂无识别方式对比', '用不同识别方式完成已标注样本后显示。');
  const metrics = [
    ['字段', 'field_accuracy', 'field_total'], ['商品', 'product_cell_accuracy', 'product_cell_total'],
    ['日期', 'date_accuracy', 'date_total'], ['印章', 'seal_conclusion_accuracy', 'seal_total'],
  ];
  return `<p class="report-card-note">各识别方式使用各自已完成的样本；比较前请留意参评数量。</p>${items.map(item => {
    const seconds = count(item.timed_samples) > 0 && Number.isFinite(item.average_seconds) ? `${item.average_seconds} 秒/张` : '暂无耗时数据';
    return `<article><div><strong>${escapeHtml(item.label)}</strong><small>${count(item.samples)} 张参评 · ${seconds}</small></div><dl>${metrics.map(([label, rate, total]) => `<div><dt>${label}<small>${item[total] == null ? '数量未提供' : `${count(item[total])} ${label === '日期' || label === '印章' ? '张' : '项'}`}</small></dt><dd>${metricValue(item[rate], item[total])}</dd></div>`).join('')}</dl></article>`;
  }).join('')}`;
}

export function createReport({ui, reportState, resultsState, api}) {
  const {$, toast} = ui;

  function setReportStatus(message, tone = 'neutral') {
    const status = $('#report-status');
    if (!status) return;
    status.textContent = message;
    status.dataset.tone = tone;
  }

  function loadReport() {
    if (reportState.reportPromise) return reportState.reportPromise;
    reportState.reportAttemptAt = Date.now();
    reportState.reportPromise = renderReport().finally(() => { reportState.reportPromise = null; });
    return reportState.reportPromise;
  }

  async function renderReport() {
    const revision = resultsState.resultRevision || 0;
    const refresh = $('#refresh-report'), grid = $('#kpi-grid');
    if (refresh) { refresh.disabled = true; refresh.textContent = '正在刷新…'; }
    grid?.setAttribute('aria-busy', 'true');
    setReportStatus(reportState.reportLoadedAt ? '正在更新统计，暂时显示上次结果…' : '正在读取评测与复核数据…');
    try {
      const report = await api('/api/report'), accuracy = report.accuracy || {}, dataset = report.dataset || {};
      const unlabeledFields = accuracy.field_coverage?.unlabeled_fields || [];
      const coverageNote = unlabeledFields.length ? ` · ${unlabeledFields.length} 项字段缺少真值` : '';
      const scope = $('#accuracy-scope');
      if (scope) {
        scope.textContent = `${accuracy.scope_backend_label || '当前识别方式'} · 已测试 ${count(accuracy.tested_samples)}/${count(accuracy.ground_truth_samples)} 张真值样本${coverageNote}`;
        scope.title = `${unlabeledFields.length ? `缺少真值：${unlabeledFields.join('、')}。` : ''}准确率只统计已标注且完成识别的样本。签收日期采用单独的日期评测。`;
      }
      const dateDetection = count(accuracy.date_present_total) > 0
        ? `检出率 ${metricValue(accuracy.date_detection_rate, accuracy.date_present_total)}（${count(accuracy.date_present_detected)} / ${count(accuracy.date_present_total)} 张有日期样本）`
        : '尚无有日期的参评样本';
      const images = count(dataset.images), labeled = count(dataset.labeled);
      const cards = [
        ['已标注字段准确率', metricValue(accuracy.field_accuracy, accuracy.field_total), sampleNote(accuracy.field_correct, accuracy.field_total, '个字段'), ''],
        ['商品单元格准确率', metricValue(accuracy.product_cell_accuracy, accuracy.product_cell_total), sampleNote(accuracy.product_cell_correct, accuracy.product_cell_total, '个单元格'), ''],
        ['日期识别准确率', metricValue(accuracy.date_accuracy, accuracy.date_total), `${sampleNote(accuracy.date_correct, accuracy.date_total, '张')} · ${dateDetection}`, ''],
        ['印章结论准确率', metricValue(accuracy.seal_conclusion_accuracy, accuracy.seal_total), sampleNote(accuracy.seal_correct, accuracy.seal_total, '张'), ''],
        ['待人工复核', count(report.review_counts?.['待复核']), `当前统计范围共 ${count(report.total_results)} 张回单`, 'is-operational'],
        ['评测真值覆盖', images > 0 && labeled <= images ? metricValue(dataset.coverage, images) : '—', `${labeled} 条真值 / ${images} 张数据图片${labeled > images ? ' · 数量范围不一致' : ''}`, ''],
      ];
      grid.innerHTML = cards.map(([label, value, note, className]) => `<div class="kpi-card ${className}${value === '—' ? ' is-empty' : ''}"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
      $('#error-stats').innerHTML = errorRows(report.error_types || [], count(report.total_results));
      $('#field-accuracy').innerHTML = accuracyRows(accuracy.field_by_name || [], 'field', '暂无字段评测数据');
      $('#product-accuracy').innerHTML = accuracyRows(accuracy.product_by_column || [], 'column', '暂无商品评测数据');
      $('#backend-comparison').innerHTML = backendRows(report.backend_comparison || []);
      reportState.reportStale = revision !== (resultsState.resultRevision || 0);
      reportState.reportLoadedAt = Date.now();
      setReportStatus(reportState.reportStale ? '统计已加载，期间回单发生变化，可刷新查看最新结果。' : `更新于 ${new Date(reportState.reportLoadedAt).toLocaleTimeString('zh-CN', {hour12: false})} · 准确率仅反映参评样本；“—”表示暂无可用评测。`);
    } catch (error) {
      reportState.reportStale = true;
      setReportStatus(reportState.reportLoadedAt ? '更新失败，当前保留上次统计。点击“刷新统计”重试。' : '统计加载失败。点击“刷新统计”重试。', 'error');
      if (!reportState.reportLoadedAt) {
        const scope = $('#accuracy-scope');
        if (scope) scope.textContent = '评测数据暂未加载';
        grid.innerHTML = emptyState('暂时无法读取统计', '请稍后点击右上角“刷新统计”重试。');
        for (const selector of ['#error-stats', '#field-accuracy', '#product-accuracy', '#backend-comparison']) $(selector).innerHTML = emptyState('数据暂未加载');
      }
      throw error;
    } finally {
      if (refresh) { refresh.disabled = false; refresh.textContent = '刷新统计'; }
      grid?.setAttribute('aria-busy', 'false');
    }
  }

  function initialize() {
    $('#refresh-report').addEventListener('click', () => loadReport().catch(error => toast(error.message, 'danger')));
  }

  return {initialize, loadReport, renderReport};
}
