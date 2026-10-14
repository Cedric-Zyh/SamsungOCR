import {createApiClients} from '../core/api.mjs';
export function createRetentionSettings({environment, ui, api, services = createApiClients(api)}) {
  services = services && typeof services === 'object' ? services : createApiClients(api);
  const {$, toast} = ui;
  let initialized = false;

  function setStatus(message, tone = '') {
    const status = $('#retention-save-status');
    status.textContent = message;
    if (tone) status.dataset.tone = tone;
    else delete status.dataset.tone;
  }

  async function save(event) {
    event?.preventDefault?.();
    const input = $('#retention-days');
    const button = $('#save-retention-days');
    const days = Number(input.value);
    if (!Number.isInteger(days) || days < 1 || days > 3650) {
      setStatus('请输入 1 至 3650 的整数', 'error');
      input.focus?.();
      return;
    }
    button.disabled = true;
    setStatus('正在保存…');
    try {
      const result = await (services.settings.update({retention_days: days}));
      input.value = String(result.retention_days);
      setStatus(`已保存，保留 ${result.retention_days} 天`);
      toast?.(`数据保留期限已更新为 ${result.retention_days} 天`);
    } catch (error) {
      setStatus(error.message || '保存失败，请稍后重试', 'error');
    } finally {
      button.disabled = false;
    }
  }

  function initialize() {
    if (initialized) return;
    initialized = true;
    $('#retention-form')?.addEventListener?.('submit', save);
    const form = $('#qingtong-form');
    async function saveQingtong(key) {
      const status = $('#qingtong-save-status');
      const buttons = environment.document.querySelectorAll('#qingtong-settings-dialog button:not([data-service-close])');
      buttons.forEach(button => { button.disabled = true; });
      status.textContent = '正在保存…';
      try {
        await api('/api/settings/qingtong', {method: 'PATCH', json: {api_key: key}});
        $('#qingtong-api-key').value = '';
        environment.location.reload();
      } catch (error) {
        status.textContent = error.message || '保存失败，请重试';
      } finally {
        buttons.forEach(button => { button.disabled = false; });
      }
    }
    form?.addEventListener?.('submit', event => {
      event.preventDefault();
      saveQingtong($('#qingtong-api-key').value.trim());
    });
    $('#clear-qingtong-key')?.addEventListener?.('click', () => saveQingtong(''));
  }

  return {initialize, save};
}
