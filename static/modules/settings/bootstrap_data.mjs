/** Transfer seed data between installations without replacing business data. */
export function initializeBootstrapData(document, window) {
  const exportButton = document.getElementById('bootstrap-export');
  const importButton = document.getElementById('bootstrap-import');
  const fileInput = document.getElementById('bootstrap-file');
  const refreshButton = document.getElementById('bootstrap-refresh');
  const status = document.getElementById('bootstrap-status');
  const scope = document.getElementById('bootstrap-scope');
  if (!exportButton || !importButton || !fileInput || !status || !refreshButton) return;
  const busy = value => {
    exportButton.disabled = value;
    importButton.disabled = value;
    if (scope) scope.disabled = value;
  };
  const failure = async response => {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || (response.status === 413 ? '数据包过大，请选择小于 450 MB 的文件' : '操作失败，请重试'));
  };
  exportButton.addEventListener('click', async () => {
    const selectedScope = scope?.value || 'all';
    busy(true);
    const label = {all:'样单和标准章', samples:'样单', standards:'标准章'}[selectedScope];
    status.textContent = `正在打包${label}，请稍候…`;
    try {
      const response = await window.fetch(`/api/bootstrap/export?scope=${encodeURIComponent(selectedScope)}`);
      if (!response.ok) await failure(response);
      const url = window.URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `samsung-receipt-${{all:'bootstrap',samples:'samples',standards:'standards'}[selectedScope]}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => window.URL.revokeObjectURL(url), 60000);
      status.textContent = '数据包已生成。在另一台电脑打开设置，点击“导入数据包”即可。';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy(false);
    }
  });
  importButton.addEventListener('click', () => fileInput.click());
  refreshButton.addEventListener('click', () => window.location.reload());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (file.size > 450 * 1024 * 1024) {
      status.textContent = '数据包超过 450 MB，请重新导出。';
      fileInput.value = '';
      return;
    }
    busy(true);
    status.textContent = '正在检查并导入数据包，请稍候…';
    refreshButton.hidden = true;
    try {
      const form = new window.FormData();
      form.append('file', file);
      form.append('scope', scope?.value || 'all');
      const response = await window.fetch('/api/bootstrap/import', {method: 'POST', body: form});
      if (!response.ok) await failure(response);
      const data = await response.json();
      status.textContent = `导入完成：新增 ${data.samples_added} 张样单、${data.standards_added} 枚标准章；跳过已有的 ${data.samples_skipped} 张样单、${data.standards_skipped} 枚标准章。点击“刷新查看”更新列表。`;
      refreshButton.hidden = false;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy(false);
      fileInput.value = '';
    }
  });
}
