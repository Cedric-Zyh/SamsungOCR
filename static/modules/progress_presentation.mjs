/** Compact workbench copy; the shared review policy still decides eligibility. */
import {providerErrorIssues} from './provider_errors.mjs';
import {providerProgress} from './provider_progress.mjs';

const optionalStageReasons = new Set([
  '部分识别：未执行项目不能据此判定整单通过',
  '本次为部分识别，未执行项目不参与整单通过判定',
]);

export function workbenchIssues(record, workbench) {
  const failures = providerErrorIssues(record);
  const issues = workbench.issues(record).filter(issue => {
    if (optionalStageReasons.has(String(issue.message).trim())) return false;
    if (failures.some(error => issue.message.includes(error.message))) return false;
    const check = record[`${issue.target}_check`];
    if (check?.status === '未执行') return false;
    return !/^部分识别：未执行项目不能据此判定整单通过/.test(issue.message);
  }).map(issue => {
    const check = record[`${issue.target}_check`] || {};
    const title = issue.target === 'seal' ? '印章' : '签收日期';
    const danger = ['不匹配', '部分匹配'].includes(check.status);
    let message = issue.message;
    if (['date', 'seal'].includes(issue.target)) {
      if (check.status === '不匹配') message = `${title}与要求不一致`;
      else if (check.status === '部分匹配') message = `${title}仅部分匹配，请确认`;
      else if (check.status === '缺少比对依据') message = issue.target === 'seal' ? '签章要求待补充' : '要求到货日期待补充';
      else if (!check.reliable && ['匹配', '未识别', '需人工复核', undefined, ''].includes(check.status)) message = issue.target === 'seal' ? '印章文字待确认' : '签收日期待确认';
    }
    return {...issue, message, danger};
  }).sort((a, b) => Number(b.danger) - Number(a.danger));
  return [...failures, ...issues];
}

export function workbenchAttention(item, {workbench, paused, uploading}) {
  const record = item.record || {}, job = item.job;
  if (uploading) return {primary: '正在上传图片', secondary: '上传期间请保持页面打开'};
  if (item.status === 'awaiting_upload') return {primary: '图片待上传', secondary: job?.start_requested === false ? '补传后点击“开始识别”' : '补传图片后继续识别'};
  if (item.status === 'ready') return {primary: '待开始', secondary: '图片已保存在本地，点击“开始识别”后处理'};
  if (item.status === 'queued') return {primary: paused ? '已暂停，等待继续' : '等待后台识别', secondary: ''};
  if (item.status === 'running') return providerProgress(job, {paused})
    || {primary: paused ? '正在完成当前图片' : '正在识别', secondary: paused ? '保存结果后暂停' : ''};
  if (item.status === 'failed') return {primary: '识别失败', secondary: job?.error_message || record.error_message || '可以重新尝试识别', danger: true};
  const issues = workbenchIssues(record, workbench);
  if (issues.length) return {
    primary: issues[0].message, danger: issues[0].danger,
    secondary: `${issues[1]?.message || ''}${issues.length > 2 ? ` · 另有 ${issues.length - 2} 项待确认` : ''}`,
    secondaryDanger: issues[1]?.danger,
  };
  const partial = ['date', 'seal'].some(key => record[`${key}_check`]?.status === '未执行')
    || (record.review_reasons || []).some(reason => !optionalStageReasons.has(String(reason).trim()) && /^部分识别：/.test(reason));
  return {primary: partial ? '已选内容识别完成，请确认结果' : '识别结果已保存，请确认', secondary: ''};
}
