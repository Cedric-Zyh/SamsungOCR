import {escapeHtml} from '../core/ui.mjs';

/** The switch represents live resources in this backend, never a saved preference. */
export function createLocalModelSettings({environment, ui, services, flushSettings = async () => {}}) {
  const {document, window, setTimeout, clearTimeout} = environment;
  const root = () => ui.$('[data-local-model-settings]');
  let data = null, submitting = false, reading = false, generation = 0, timer = null, initialized = false;
  const node = selector => root()?.querySelector(selector);
  const visible = () => !document.hidden && (environment.location?.hash || window.location?.hash) === '#settings';
  const cancel = () => { if (timer != null) clearTimeout(timer); timer = null; };

  function render(error = '') {
    if (!root()) return;
    const toggle = node('[data-local-model-toggle]');
    const state = data?.state || 'unknown';
    const working = submitting || Boolean(data?.operation);
    toggle.checked = Boolean(data?.loaded);
    toggle.disabled = !data || working || Boolean(data.busy) || (!data.required_count && !data.loaded);
    const stateNode = node('[data-local-model-state]');
    stateNode.dataset.state = state;
    stateNode.textContent = submitting ? '正在提交…' : !data ? (error ? '状态未读取' : '正在读取…')
      : ({loading:'正在初始化…',unloading:'正在释放…',busy:'识别中',ready:'已就绪',partial:'部分已加载',unloaded:'未加载',unneeded:'无需加载'})[state] || '状态未读取';
    node('[data-local-model-description]').textContent = data?.ready
      ? '已按当前方案加载，后续回单直接复用。'
      : data && !data.required_count ? '当前方案仅使用远程服务。' : '提前准备当前方案需要的本地模型。';
    node('[data-local-model-note]').textContent = data?.busy
      ? '本地识别运行中，完成任务后可释放模型。'
      : '关闭可释放模型；再次本地识别时会自动加载。';
    const prepare = node('[data-local-model-prepare]');
    prepare.hidden = !data?.loaded || data.ready || !data.required_count || working || data.busy;
    node('[data-local-model-list]').innerHTML = data?.models?.length
      ? data.models.map(model => `<div><span>${escapeHtml(model.label)}${model.required ? '' : '<small>其他已加载</small>'}</span><b>${model.loaded ? '已加载' : '未加载'}</b></div>`).join('')
      : data ? '当前没有需要准备的本地模型。' : '正在读取…';
    const seconds = data?.initialization_seconds;
    const duration = node('[data-local-model-duration]');
    duration.hidden = typeof seconds !== 'number' || !Number.isFinite(seconds);
    duration.textContent = duration.hidden ? '' : `最近初始化 ${Number(seconds.toFixed(2))} 秒`;
    node('[data-local-model-feedback]').textContent = error || (!data?.ready && data?.error) || '';
  }

  function schedule() {
    cancel();
    if (visible()) timer = setTimeout(() => load(), data?.operation ? 600 : 2500);
  }

  async function load() {
    cancel();
    if (!root() || !visible() || reading || submitting) return;
    const serial = generation;
    reading = true;
    try {
      const response = await services.settings.localModels();
      if (serial !== generation) return;
      data = response;
      render();
    } catch (error) {
      if (serial === generation) {
        data = null;
        render(error.status === 404 ? '模型控制服务尚未更新，请重新打开应用。' : error.message);
      }
    } finally {
      reading = false;
      if (serial === generation) schedule();
    }
  }

  async function change(enabled) {
    if (submitting || !data || data.busy || data.operation) {render();return;}
    ++generation; cancel(); submitting = true; render();
    try {
      await flushSettings();
      data = await services.settings.setLocalModels(enabled);
      render();
    } catch (error) {
      if (error.status === 409 && error.details?.state) data = error.details;
      render(error.message);
    } finally {
      submitting = false;
      render(node('[data-local-model-feedback]').textContent);
      schedule();
    }
  }

  function initialize() {
    if (initialized || !root()?.querySelector || !node('[data-local-model-toggle]')) return;
    initialized = true;
    node('[data-local-model-toggle]').addEventListener('change', event => change(event.target.checked));
    node('[data-local-model-prepare]').addEventListener('click', () => change(true));
    const refresh = () => {cancel();if(visible())load();};
    window.addEventListener('hashchange', refresh);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    render();
    refresh();
  }
  return {initialize, load};
}
