import {escapeHtml, planLabels} from './ui.mjs';

const methodLabels = {paddle:'Paddle Mobile', paddle_server:'Paddle Server', vision:'Vision', qingtong:'清瞳', danzhengtong:'单证通'};
const presetLabels = {danzhengtong:'单证通',full:'完整核验', date:'仅日期', seal:'仅印章', 'seal-test':'印章测试'};
const localMethods = ['paddle', 'vision', 'paddle_server'];
const storageKey = 'receipt-recognition-plan';
const acceptanceStorageKey = 'receipt-acceptance-policy';

export function createRecognitionPlan({environment, ui, importsState}) {
  const {localStorage} = environment;
  const {$, $$} = ui;
  const stages = Object.keys(planLabels);
  let initialSaveStatus = '';
  let initialSaveState = 'ready';

  const stageMethods = stage => $$(`[data-stage="${stage}"]`);
  const available = (stage, method) => stageMethods(stage).some(el => el.dataset.method === method && el.dataset.available === 'true');
  const supported = (stage, method) => localMethods.includes(method) || (stage === 'seal' && method === 'qingtong') || (stage !== 'products' && method === 'danzhengtong');
  const selected = stage => stageMethods(stage).filter(el => el.checked && el.dataset.available === 'true' && supported(stage, el.dataset.method)).map(el => el.dataset.method);
  const enabled = stage => !!$(`[data-target="${stage}"]`)?.checked;
  const setText = (selector, value) => { const node = $(selector); if (node) node.textContent = value; };
  const show = (selector, visible) => $(selector)?.classList.toggle('hidden', !visible);
  const samePlan = (left, right) => stages.every(stage => left[stage].length === right[stage].length && left[stage].every(method => right[stage].includes(method)));

  function readRecognitionPlan() {
    const plan = {};
    for (const stage of stages) {
      plan[stage] = enabled(stage) ? selected(stage) : [];
      if (enabled(stage) && !plan[stage].length) throw new Error(`请为${planLabels[stage]}选择至少一种识别方式`);
    }
    if (!Object.values(plan).some(methods => methods.length)) throw new Error('请至少选择一项识别内容');
    plan.acceptance = readAcceptancePolicy();
    return plan;
  }
  function readAcceptancePolicy() {
    const readMode = stage => {
      const checked = $$(`[data-acceptance-input="${stage}"]`).find(input => input.checked);
      return ['any', 'all', 'none'].includes(checked?.value) ? checked.value : 'any';
    };
    const selectedReject = $$('[data-acceptance-reject]').find(input => input.checked)?.value;
    return {
      seal_match_mode: readMode('seal'),
      date_match_mode: readMode('date'),
      signature_match_mode: readMode('signature'),
      reject_mode: ['any_mismatch', 'all_mismatch', 'none'].includes(selectedReject) ? selectedReject : 'none',
      // Keep legacy keys for tasks/settings written by older versions.
      seal_pass_standard: 'any_exact',
      date_source: 'danzhengtong',
    };
  }
  function usesRemotePlan() { return stages.some(stage => enabled(stage) && selected(stage).some(method => ['qingtong', 'danzhengtong'].includes(method))); }

  function applyPlan(plan, {persist = true} = {}) {
    for (const stage of stages) {
      const methods = Array.isArray(plan?.[stage]) ? plan[stage].filter(method => typeof method === 'string' && supported(stage, method)) : [];
      const target = $(`[data-target="${stage}"]`);
      if (target) target.checked = methods.length > 0;
      stageMethods(stage).forEach(el => el.checked = el.dataset.available === 'true' && methods.includes(el.dataset.method));
    }
    updateRecognitionPlan({persist});
  }
  function defaultPlan() {
    const choose = (stage, preference) => {
      const method = preference.find(method => available(stage, method));
      return method ? [method] : [];
    };
    return {
      fields:choose('fields', localMethods), products:choose('products', localMethods), handwriting:[],
      date:choose('date', ['danzhengtong', 'vision', 'paddle', 'paddle_server']), seal:choose('seal', ['vision', 'paddle', 'paddle_server']),
    };
  }
  function presetPlan(name) {
    if (name === 'danzhengtong') return {fields:['danzhengtong'], products:[], handwriting:['danzhengtong'], date:['danzhengtong'], seal:['danzhengtong']};
    if (name === 'seal-test') return {fields:['vision'], products:[], handwriting:[], date:[], seal:['qingtong']};
    const plan = defaultPlan();
    if (name === 'full') {
      const method = ['vision', 'paddle', 'paddle_server'].find(method => available('handwriting', method));
      plan.handwriting = method ? [method] : [];
    } else for (const stage of stages) if (stage !== name) plan[stage] = [];
    return plan;
  }
  function presetUnavailable(name) {
    if (name === 'seal-test') {
      const missing = [];
      if (!available('fields', 'vision')) missing.push('Vision 不可用');
      if (!available('seal', 'qingtong')) missing.push('清瞳尚未配置');
      return missing.join('，');
    }
    const plan = presetPlan(name);
    return Object.values(plan).some(methods => methods.length) ? '' : '暂无可用的本地识别方式';
  }
  function updateRecognitionPlan({persist = true} = {}) {
    const locked = !!importsState.batchRunning;
    $$('.recognition-plan button, [data-target]').forEach(el => el.disabled = locked);
    $$('[data-preset]').forEach(button => {
      const reason = presetUnavailable(button.dataset.preset);
      button.disabled = locked || !!reason;
      button.title = locked ? '上传期间暂不能修改识别配置' : reason;
      if (button.dataset.preset === 'seal-test') {
        setText('#seal-test-availability', reason);
        show('#seal-test-availability', !!reason);
      }
    });
    let selectedCount = 0;
    const current = {};
    for (const stage of stages) {
      const active = enabled(stage);
      current[stage] = active ? selected(stage) : [];
      if (active) selectedCount++;
      stageMethods(stage).forEach(el => el.disabled = locked || !active || el.dataset.available !== 'true');
      const invalid = active && !current[stage].length;
      $(`[data-plan-card="${stage}"]`)?.classList.toggle('inactive', !active);
      $(`[data-plan-card="${stage}"]`)?.classList.toggle('invalid', invalid);
      $(`[data-target="${stage}"]`)?.setAttribute('aria-invalid', String(invalid));
      setText(`[data-plan-status="${stage}"]`, !active ? '未启用' : invalid ? '请选择方式' : '已启用');
    }
    setText('#plan-selected-count', `${selectedCount} / ${stages.length} 项`);
    const list = $('#plan-selection-list');
    if (list) list.innerHTML = stages.map(stage => `<div class="plan-selection-row${!enabled(stage) ? ' inactive' : ''}"><span>${escapeHtml(planLabels[stage])}</span><strong>${escapeHtml(!enabled(stage) ? '未执行' : current[stage].length ? current[stage].map(method => methodLabels[method]).join(' + ') : '请选择方式')}</strong></div>`).join('');
    show('#remote-plan-note', usesRemotePlan());
    show('#plan-multi-note', Object.values(current).some(methods => methods.length > 1));
    show('#plan-requirement-note', (enabled('date') || enabled('seal')) && !enabled('fields'));
    show('#plan-page-note', Object.values(current).some(methods => methods.some(method => localMethods.includes(method))));

    let plan, errorMessage = '';
    try { plan = readRecognitionPlan(); } catch (error) { errorMessage = error.message; }
    show('#plan-error', !!errorMessage);
    setText('#plan-error', errorMessage);
    for (const button of $$('[data-preset]')) {
      const active = !!plan && !presetUnavailable(button.dataset.preset) && samePlan(plan, presetPlan(button.dataset.preset));
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
    const match = plan && Object.keys(presetLabels).find(name => !presetUnavailable(name) && samePlan(plan, presetPlan(name)));
    setText('#plan-name', errorMessage ? '待完善' : match ? presetLabels[match] : samePlan(plan, defaultPlan()) ? '默认方案' : '自定义');
    if (!plan) {
      setText('#plan-summary', errorMessage);
      setText('#import-config', `配置未完成：${errorMessage}`);
      setText('#plan-save-status', '配置未完成，尚未保存');
      $('#plan-save-status')?.setAttribute('data-state', 'invalid');
      return;
    }
    const summary = Object.entries(plan).filter(([,methods]) => methods.length).map(([stage,methods]) => `${planLabels[stage]}（${methods.map(method => methodLabels[method]).join(' + ')}）`).join('；');
    setText('#plan-summary', `本次配置：${summary}`);
    setText('#import-config', summary);
    let saveStatus = initialSaveStatus || '使用默认方案，修改后自动保存', saveState = initialSaveState;
    if (persist) {
      try {
        localStorage.setItem(storageKey, JSON.stringify(plan));
        saveStatus = '已自动保存到当前浏览器'; saveState = 'saved';
      } catch (_) {
        saveStatus = '当前选择可用，但未能保存到浏览器'; saveState = 'error';
      }
    }
    setText('#plan-save-status', saveStatus);
    $('#plan-save-status')?.setAttribute('data-state', saveState);
  }

  function initialize() {
    $$('.recognition-plan input').forEach(el => el.addEventListener('change', () => updateRecognitionPlan()));
    $$('[data-acceptance-input], [data-acceptance-reject]').forEach(input => input.addEventListener('change', () => {
      try { localStorage.setItem(acceptanceStorageKey, JSON.stringify(readAcceptancePolicy())); } catch (_) { /* browser storage unavailable */ }
      updateRecognitionPlan({persist: false});
    }));
    $$('[data-preset]').forEach(button => button.addEventListener('click', () => {
      if (importsState.batchRunning || presetUnavailable(button.dataset.preset)) return;
      applyPlan(presetPlan(button.dataset.preset));
    }));
    $$('[data-plan-reset]').forEach(button => button.addEventListener('click', () => {
      if (!importsState.batchRunning) applyPlan(defaultPlan());
    }));
    let savedPlan;
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
        savedPlan = saved;
        initialSaveStatus = '已载入当前浏览器的设置';
      }
    } catch (_) {
      initialSaveStatus = '未能读取已保存设置，当前使用默认方案';
      initialSaveState = 'error';
    }
    applyPlan(savedPlan || defaultPlan(), {persist:false});
    try {
      const policy = JSON.parse(localStorage.getItem(acceptanceStorageKey) || 'null');
      for (const stage of ['seal', 'date', 'signature']) {
        const savedMode = policy?.[`${stage}_match_mode`];
        const mode = ['any', 'all', 'none'].includes(savedMode) ? savedMode : 'any';
        $$(`[data-acceptance-input="${stage}"]`).forEach(input => { input.checked = input.value === mode; });
      }
      const rejectMode = ['any_mismatch', 'all_mismatch', 'none'].includes(policy?.reject_mode) ? policy.reject_mode : 'none';
      $$('[data-acceptance-reject]').forEach(input => { input.checked = input.value === rejectMode; });
    } catch (_) { /* use defaults */ }
  }

  return {initialize, readRecognitionPlan, readAcceptancePolicy, usesRemotePlan, applyPlan, defaultPlan, updateRecognitionPlan};
}
