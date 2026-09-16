const stages = {
  uploading: '上传文件', submitting: '提交识别', waiting: '等待识别结果',
  paused: '已暂停查询', completed: '结果已返回', failed: '识别失败',
};

function duration(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  return `${minutes} 分钟${rest ? ` ${rest} 秒` : ''}`;
}

export function providerProgress(job, {paused = false} = {}) {
  const progress = job?.progress;
  if (job?.status !== 'running' || progress?.provider !== 'danzhengtong' || !stages[progress.stage]) return null;
  const parts = [];
  if (['waiting', 'paused'].includes(progress.stage)) {
    parts.push(`已等待 ${duration(progress.elapsed_seconds)}`);
    if (progress.timeout_seconds > 0) parts.push(`等待上限 ${duration(progress.timeout_seconds)}`);
    if (progress.stage === 'paused') parts.push(`已查询 ${progress.poll_count || 0} 次 · 暂停期间不查询，总等待时间不重置`);
    else parts.push(progress.poll_count > 0 ? `已查询 ${progress.poll_count} 次，尚未返回完整结果` : '已受理，正在等待返回');
  } else if (progress.stage === 'failed') {
    if (progress.error_message) parts.push(progress.error_message);
    parts.push('正在处理其余项目');
  } else if (progress.stage === 'completed') {
    parts.push('正在处理其余项目并整理回单');
  } else {
    parts.push(`已用时 ${duration(progress.elapsed_seconds)}`);
  }
  if (paused && progress.stage !== 'paused') parts.push('不再发起后续查询，当前已发出的请求结束后暂停');
  const label = paused && progress.stage === 'waiting' ? '正在暂停查询' : stages[progress.stage];
  return {primary: `单证通${progress.simulated ? '（模拟）' : ''}：${label}`,
    secondary: parts.join(' · '), danger: progress.stage === 'failed'};
}
