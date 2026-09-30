import {escapeHtml, planLabels} from '../core/ui.mjs';

const methodLabels = {paddle_v6:'Paddle v6 Small', paddle_seal:'Paddle 印章专用', qingtong:'清瞳', danzhengtong:'单证通'};
const sealOrientationLabels = {none:'不处理', polygon:'文本框角度估计', doc_ori:'文档方向分类（doc_ori）', combined:'两者结合'};
const presetLabels = {danzhengtong:'单证通',full:'完整核验', date:'仅日期', seal:'仅印章', 'seal-test':'印章测试'};
const localMethods = ['paddle_v6', 'paddle_seal'];
const storageKey = 'receipt-recognition-plan';
const acceptanceStorageKey = 'receipt-acceptance-policy';

export function createRecognitionPlan({environment, ui, importsState, services = null}) {
  const {localStorage} = environment;
  const {$, $$} = ui;
  const stages = Object.keys(planLabels);
  let initialSaveStatus = '';
  let initialSaveState = 'ready';
  let serverSave = Promise.resolve();

  const stageMethods = stage => $$(`[data-stage="${stage}"]`);
  const available = (stage, method) => stageMethods(stage).some(el => el.dataset.method === method && el.dataset.available === 'true');
  const supported = (stage, method) => (method === 'paddle_seal' ? stage === 'seal' : localMethods.includes(method)) || (stage === 'seal' && method === 'qingtong') || (stage !== 'products' && method === 'danzhengtong');
  const selected = stage => stageMethods(stage).filter(el => el.checked && el.dataset.available === 'true' && supported(stage, el.dataset.method)).map(el => el.dataset.method);
  const enabled = stage => !!$(`[data-target="${stage}"]`)?.checked;
  const setText = (selector, value) => { const node = $(selector); if (node) node.textContent = value; };
  const show = (selector, visible) => $(selector)?.classList.toggle('hidden', !visible);
  const samePlan = (left, right) => stages.every(stage => left[stage].length === right[stage].length && left[stage].every(method => right[stage].includes(method)))
    && (left.seal_orientation || 'polygon') === (right.seal_orientation || 'polygon');

  function readRecognitionPlan() {
    const plan = {};
    for (const stage of stages) {
      plan[stage] = enabled(stage) ? selected(stage) : [];
      if (enabled(stage) && !plan[stage].length) throw new Error(`请为${planLabels[stage]}选择至少一种识别方式`);
    }
    const orientationSelect = $('#seal-orientation-mode');
    if (orientationSelect) {
      const orientation = orientationSelect.value;
      plan.seal_orientation = Object.prototype.hasOwnProperty.call(sealOrientationLabels, orientation)
        ? orientation : 'polygon';
    }
    if (!stages.some(stage => plan[stage].length)) throw new Error('请至少选择一项识别内容');
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
      low_confidence_mode: $$('[data-acceptance-low-confidence]').find(input => input.checked)?.value === 'ignore' ? 'ignore' : 'check',
      // Keep legacy keys for tasks/settings written by older versions.
      seal_pass_standard: 'any_exact',
      date_source: 'danzhengtong',
    };
  }
  function defaultAcceptancePolicy() {
    return {
      seal_match_mode: 'any', date_match_mode: 'any', signature_match_mode: 'none',
      reject_mode: 'any_mismatch', low_confidence_mode: 'ignore',
      seal_pass_standard: 'any_exact', date_source: 'danzhengtong',
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
    const orientation = $('#seal-orientation-mode');
    if (orientation && Object.prototype.hasOwnProperty.call(sealOrientationLabels, plan?.seal_orientation)) {
      orientation.value = plan.seal_orientation;
    }
    updateRecognitionPlan({persist});
  }
  function defaultPlan() {
    const choose = stage => {
      const defaults = {
        fields: ['paddle_v6'], products: [], handwriting: ['paddle_v6'],
        date: ['paddle_v6'], seal: ['paddle_v6', 'qingtong'],
      }[stage] || [];
      return defaults.filter(method => available(stage, method));
    };
    const plan = {
      fields:choose('fields'), products:choose('products'), handwriting:choose('handwriting'),
      date:choose('date'), seal:choose('seal'),
    };
    if ($('#seal-orientation-mode')) plan.seal_orientation = 'polygon';
    return plan;
  }

  function applyAcceptancePolicy(policy) {
    if (!policy || typeof policy !== 'object') return;
    for (const stage of ['seal', 'date', 'signature']) {
      const savedMode = policy[`${stage}_match_mode`];
      const mode = ['any', 'all', 'none'].includes(savedMode) ? savedMode : 'any';
      $$(`[data-acceptance-input="${stage}"]`).forEach(input => { input.checked = input.value === mode; });
    }
    const rejectMode = ['any_mismatch', 'all_mismatch', 'none'].includes(policy.reject_mode) ? policy.reject_mode : 'none';
    $$('[data-acceptance-reject]').forEach(input => { input.checked = input.value === rejectMode; });
    const lowConfidenceMode = policy.low_confidence_mode === 'ignore' ? 'ignore' : 'check';
    $$('[data-acceptance-low-confidence]').forEach(input => { input.checked = input.value === lowConfidenceMode; });
  }

  function queueServerSave() {
    if (!services?.settings?.updateRecognition) return;
    let plan;
    try { plan = readRecognitionPlan(); } catch (_) { return; }
    const payload = {recognition_config: {...plan}};
    delete payload.recognition_config.acceptance;
    payload.acceptance_policy = plan.acceptance;
    serverSave = serverSave.then(() => services.settings.updateRecognition(payload)).catch(() => {
      // Browser storage remains available as a fallback when the local server
      // is restarting; the next change retries the durable save.
    });
  }
  function presetPlan(name) {
    if (name === 'danzhengtong') return {fields:['danzhengtong'], products:[], handwriting:['danzhengtong'], date:['danzhengtong'], seal:['danzhengtong']};
    if (name === 'seal-test') return {fields:['paddle_v6'], products:[], handwriting:[], date:[], seal:['qingtong']};
    const plan = defaultPlan();
    if (name === 'full') {
      const method = ['paddle_v6'].find(method => available('handwriting', method));
      plan.handwriting = method ? [method] : [];
    } else for (const stage of stages) if (stage !== name) plan[stage] = [];
    return plan;
  }
  function presetUnavailable(name) {
    if (name === 'seal-test') {
      const missing = [];
      if (!available('fields', 'paddle_v6')) missing.push('Paddle v6 不可用');
      if (!available('seal', 'qingtong')) missing.push('清瞳尚未配置');
      return missing.join('，');
    }
    const plan = presetPlan(name);
    return Object.values(plan).some(methods => Array.isArray(methods) && methods.length) ? '' : '暂无可用的本地识别方式';
  }
  function updateRecognitionPlan({persist = true} = {}) {
    const locked = !!importsState.batchRunning;
    $$('.recognition-plan button, [data-target]').forEach(el => el.disabled = locked);
    if ($('#seal-orientation-mode')) $('#seal-orientation-mode').disabled = locked;
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
    const orientation = $('#seal-orientation-mode')?.value || 'polygon';
    if (list) list.innerHTML = stages.map(stage => `<div class="plan-selection-row${!enabled(stage) ? ' inactive' : ''}"><span>${escapeHtml(planLabels[stage])}</span><strong>${escapeHtml(!enabled(stage) ? '未执行' : current[stage].length ? current[stage].map(method => methodLabels[method]).join(' + ') : '请选择方式')}</strong></div>`).join('')
      + `<div class="plan-selection-row"><span>印章方向</span><strong>${escapeHtml(sealOrientationLabels[orientation] || orientation)}</strong></div>`;
    const orientationDescription = $('#seal-orientation-description');
    if (orientationDescription) orientationDescription.textContent = orientation === 'none'
      ? '不改变印章方向，直接识别。'
      : orientation === 'doc_ori'
        ? '使用 PP-LCNet_x1_0_doc_ori，置信度达到 90% 时按 0/90/180/270° 粗校正；低于阈值保留原方向。圆章环形文字仍可能误判，不能校正任意倾斜角。'
        : orientation === 'combined'
          ? '对 0/90/180/270 四个方向各做一次粗校正，再按“用章/专用章”文本框角度微调，取章型行识别最好的一档。'
          : '使用“用章/专用章”文本检测框的四点坐标估计角度。';
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
    setText('#plan-name', errorMessage ? '待完善' : samePlan(plan, defaultPlan()) ? '默认方案' : match ? presetLabels[match] : '自定义');
    if (!plan) {
      setText('#plan-summary', errorMessage);
      setText('#import-config', `配置未完成：${errorMessage}`);
      setText('#plan-save-status', '配置未完成，尚未保存');
      $('#plan-save-status')?.setAttribute('data-state', 'invalid');
      return;
    }
    const summary = stages.filter(stage => plan[stage].length).map(stage => `${planLabels[stage]}（${plan[stage].map(method => methodLabels[method]).join(' + ')}）`).join('；')
      + `；印章方向（${sealOrientationLabels[plan.seal_orientation || 'polygon']}）`;
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
      queueServerSave();
    }
    setText('#plan-save-status', saveStatus);
    $('#plan-save-status')?.setAttribute('data-state', saveState);
  }

  function initialize() {
    if ($('#seal-orientation-mode')) $('#seal-orientation-mode').value = 'polygon';
    $$('.recognition-plan input').forEach(el => el.addEventListener('change', () => updateRecognitionPlan()));
    $('#seal-orientation-mode')?.addEventListener('change', () => updateRecognitionPlan());
    $$('[data-acceptance-input], [data-acceptance-reject], [data-acceptance-low-confidence]').forEach(input => input.addEventListener('change', () => {
      try { localStorage.setItem(acceptanceStorageKey, JSON.stringify(readAcceptancePolicy())); } catch (_) { /* browser storage unavailable */ }
      updateRecognitionPlan({persist: false});
      queueServerSave();
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
      const storedPolicy = JSON.parse(localStorage.getItem(acceptanceStorageKey) || 'null');
      const policy = storedPolicy?.acceptance && typeof storedPolicy.acceptance === 'object'
        ? storedPolicy.acceptance : storedPolicy;
      const hasPolicy = policy && typeof policy === 'object' && !Array.isArray(policy)
        && ['seal_match_mode', 'date_match_mode', 'signature_match_mode', 'reject_mode', 'low_confidence_mode']
          .some(key => Object.prototype.hasOwnProperty.call(policy, key));
      applyAcceptancePolicy(hasPolicy ? policy : defaultAcceptancePolicy());
    } catch (_) { applyAcceptancePolicy(defaultAcceptancePolicy()); }

    // The local service is the durable source of truth.  Load it after the
    // synchronous browser-cache paint so the settings page remains responsive
    // even while PyCharm is starting the Flask process.
    if (services?.settings?.recognition) {
      // Defer the call itself as well as its response handling.  API clients
      // begin their fetch synchronously, and initialization must remain a
      // side-effect-free paint step for embedded callers and tests.
      Promise.resolve().then(() => services.settings.recognition()).then(settings => {
        if (!settings?.recognition_config) return;
        applyAcceptancePolicy(settings.acceptance_policy);
        applyPlan(settings.recognition_config, {persist:false});
        try {
          localStorage.setItem(storageKey, JSON.stringify(settings.recognition_config));
          localStorage.setItem(acceptanceStorageKey, JSON.stringify(settings.acceptance_policy || {}));
        } catch (_) { /* browser storage unavailable */ }
        initialSaveStatus = '已载入本地服务保存的默认配置';
        initialSaveState = 'saved';
        updateRecognitionPlan({persist:false});
      }).catch(() => {
        // A server started from an older version has no settings endpoint;
        // keep using the browser cache/defaults until it is upgraded.
      });
    }
  }

  return {initialize, readRecognitionPlan, readAcceptancePolicy, usesRemotePlan, applyPlan, defaultPlan, updateRecognitionPlan};
}
