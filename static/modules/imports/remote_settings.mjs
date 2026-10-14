export function createRemoteSettings({environment, ui, api}) {
  const {$, $$} = ui;
  let initialized = false;
  function initialize() {
    if (initialized) return;
    initialized = true;
    const form = $('#dzt-config-form');
    if (!form) return;
    $$('[data-service-dialog]').forEach(button => {
      button.addEventListener('click', () => {
        const dialog = environment.document.getElementById(button.dataset.serviceDialog);
        if (dialog && !dialog.open) dialog.showModal();
      });
    });
    $$('[data-service-close]').forEach(button => {
      button.addEventListener('click', () => button.closest('dialog').close());
    });
    const testForm = $('#dzt-test-form');
    const status = $('#dzt-save-status');
    $('#dzt-test-file').addEventListener('change', event => {
      $('#dzt-test-filename').textContent = event.target.files[0]?.name || '未选择图片';
    });
    async function run(action, reload = true) {
      const buttons = [...environment.document.querySelectorAll('#dzt-settings-dialog button:not([data-service-close])')];
      const previous = buttons.map(button => button.disabled);
      buttons.forEach(button => { button.disabled = true; });
      status.textContent = reload ? '正在保存…' : '正在测试：上传、提交并等待识别结果…';
      try {
        const result = await action();
        status.textContent = result.message || '已保存';
        if (reload) environment.location.reload();
        else environment.document.querySelectorAll('[data-dzt-status]').forEach(node => { node.textContent = result.status; });
      } catch (error) {
        status.textContent = error.message || '操作失败，请重试';
        if (!reload) environment.document.querySelectorAll('[data-dzt-status]').forEach(node => { node.textContent = '请重新测试'; });
      } finally {
        buttons.forEach((button, index) => { button.disabled = previous[index]; });
      }
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      const payload = Object.fromEntries(new environment.window.FormData(form));
      payload.poll_timeout_seconds = Number(payload.poll_timeout_seconds);
      run(() => api('/api/settings/danzhengtong', {method:'PATCH', json:payload}));
    });
    $('#dzt-clear').addEventListener('click', () => run(() => api('/api/settings/danzhengtong', {method:'PATCH', json:{clear:true}})));
    testForm.addEventListener('submit', event => {
      event.preventDefault();
      const data = new environment.window.FormData();
      data.append('file', $('#dzt-test-file').files[0]);
      run(() => api('/api/settings/danzhengtong/test', {method:'POST', body:data}), false);
    });
  }
  return {initialize};
}
