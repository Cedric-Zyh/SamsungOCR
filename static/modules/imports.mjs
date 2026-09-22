import {localToday} from './ui.mjs';
import {confirmInline} from './inline_confirm.mjs';

export function createImports({
  environment, ui, importsState, recordsState, progressState = {}, api, ReceiptImport, readRecognitionPlan, readAcceptancePolicy,
  usesRemotePlan, updateRecognitionPlan, loadDailyResults, showQueuedRetry, closeModal,
  syncStartRecognition = () => {}
}) {
  const {document, window, location, localStorage, FormData} = environment;
  const {$, $$, toast} = ui;
  const fileInput = $('#file-input'), folderInput = $('#folder-input'), dropZone = $('#drop-zone');

  async function importFiles(selection, name) {
    if (importsState.batchRunning || importsState.importScanning) return toast('正在导入，请等待当前上传完成');
    importsState.importScanning = true;
    try {
      syncStartRecognition();
      $('#choose-file').disabled = $('#choose-folder').disabled = true;
      $('#folder-scan-status').textContent = '正在读取图片和子文件夹…';
      const {files, errors} = await selection;
      const {images, skipped} = ReceiptImport.summarize(files);
      const summary = `找到 ${images.length} 张图片${skipped ? `，跳过 ${skipped} 个不支持的文件` : ''}${errors.length ? `，${errors.length} 项读取失败` : ''}`;
      $('#folder-scan-status').textContent = summary;
      if (errors.length) toast(`${summary}。${errors[0].path}：${errors[0].message}`, 'warning');
      importsState.importScanning = false;
      importsState.importSummary = summary;
      await startBatch(images, [], name);
    } catch (error) {
      $('#folder-scan-status').textContent = '读取失败，请重新选择文件夹';
      toast(error.message, 'danger');
    } finally {
      importsState.importScanning = false;
      importsState.importSummary = '';
      $('#choose-file').disabled = $('#choose-folder').disabled = importsState.batchRunning;
      syncStartRecognition();
    }
  }

  async function startBatch(files, samples, name) {
    if (importsState.importScanning) return toast('正在读取文件夹，请稍候');
    try { await runBatch(files, samples, name); } catch (error) { toast(error.message, 'danger'); }
  }

  async function runBatch(files, samples, name) {
    files = files.filter(ReceiptImport.isImage);
    const items = [...files.map(file => ({name: ReceiptImport.relativePath(file), file})), ...samples.map(sample => ({name: sample, sample}))];
    fileInput.value = ''; folderInput.value = '';
    if (!items.length) return toast('没有找到支持的图片', 'warning');
    if (importsState.batchRunning) return toast('图片正在上传，请等待上传完成', 'warning');
    const plan = readRecognitionPlan();
    importsState.batchRunning = true;
    importsState.uploadingJobs = new Set();
    try {
      syncStartRecognition();
      $('#choose-file').disabled = $('#choose-folder').disabled = true;
      updateRecognitionPlan();
      let failures = 0;
      const groups = ReceiptImport.batches(items);
      for (const [groupIndex, group] of groups.entries()) {
        const task = await api('/api/tasks', {method: 'POST', json: {total: group.length, name: groups.length > 1 ? `${name}（${groupIndex + 1}/${groups.length}）` : name, background: true,
          items: group.map(item => ({filename: item.name})), ocr_backend: importsState.ocrBackend,
          seal_recognition_mode: importsState.sealRecognitionMode, recognition_config: plan, acceptance_policy: readAcceptancePolicy?.()}});
        // An older running server may still auto-start uploads. Require the
        // durable held-job contract before sending any local image bytes.
        if (!Array.isArray(task.items) || task.items.length !== group.length
            || task.items.some(job => job.start_requested !== false)) {
          throw new Error('服务尚未支持“导入后开始”，请重新加载服务后再导入。图片尚未上传。');
        }
        importsState.taskId = task.id;
        importsState.taskImportDate = (task.created_at || '').slice(0, 10) || localToday();
        progressState.workFilters = null;
        progressState.workFilter = 'processing';
        progressState.workVisibleLimit = 50;
        progressState.dailyReady = false;
        $('#import-dialog').close();
        $('#progress-date').value = importsState.taskImportDate;
        location.hash = 'progress';
        setBatchStep(2);
        try { localStorage.setItem('receipt-progress-date', importsState.taskImportDate); } catch (_) {}
        await runPool(group.map((item, index) => async () => {
          const job = task.items[index];
          importsState.uploadingJobs.add(job.id);
          const form = new FormData();
          if (item.file) form.append('file', item.file); else form.append('sample', item.sample);
          try { await api(`/api/jobs/${job.id}/upload`, {method: 'POST', body: form}); }
          catch (_) { failures++; }
          finally { importsState.uploadingJobs.delete(job.id); }
        }), 2);
      }
      toast(failures ? `${failures} 张图片的上传未确认，请查看并补传。已导入的图片等待你点击“开始识别”。`
        : `${importsState.importSummary ? importsState.importSummary + '。' : ''}已导入 ${items.length} 张图片，点击“开始识别”后处理。`, failures ? 'warning' : 'success');
    } finally {
      importsState.batchRunning = false;
      importsState.uploadingJobs = new Set();
      $('#choose-file').disabled = $('#choose-folder').disabled = false;
      updateRecognitionPlan();
      syncStartRecognition();
      await loadDailyResults();
    }
  }

  async function runPool(tasks, concurrency) {
    let cursor = 0;
    async function worker() { while (cursor < tasks.length) { const index = cursor++; await tasks[index](); } }
    await Promise.all(Array.from({length: Math.min(concurrency, tasks.length)}, worker));
  }

  async function retryOne(id, button) {
    if (usesRemotePlan() && !await confirmInline({environment, anchor: button, message: '重新识别会将这张完整回单上传至所选远程识别服务。'})) return;
    if (button) button.disabled = true;
    try {
      const task = await api(`/api/results/${id}/retry`, {method: 'POST', json: {background: true,
        ocr_backend: importsState.ocrBackend, seal_recognition_mode: importsState.sealRecognitionMode, recognition_config: readRecognitionPlan(), acceptance_policy: readAcceptancePolicy?.()}});
      closeModal(); await showQueuedRetry(task);
    } catch (error) { toast(error.message, 'danger'); }
    finally { if (button) button.disabled = false; }
  }

  async function bulkRetry() {
    if (!recordsState.selected.size) return toast('请先勾选回单', 'warning');
    const button = $('#bulk-retry'); button.disabled = true;
    if (usesRemotePlan() && !await confirmInline({environment, anchor: button, message: `批量重新识别会将 ${recordsState.selected.size} 张完整回单上传至所选远程识别服务。`})) { button.disabled = false; return; }
    try {
      const task = await api('/api/results/bulk-retry', {method: 'POST', json: {background: true,
        ids: [...recordsState.selected], ocr_backend: importsState.ocrBackend, seal_recognition_mode: importsState.sealRecognitionMode, recognition_config: readRecognitionPlan(), acceptance_policy: readAcceptancePolicy?.()}});
      await showQueuedRetry(task);
    } catch (error) { toast(error.message, 'danger'); }
    finally { button.disabled = false; }
  }

  function setBatchStep(step) {
    $$('[data-step]').forEach(item => {
      item.classList.toggle('active', Number(item.dataset.step) === step);
      item.classList.toggle('complete', Number(item.dataset.step) < step);
    });
  }

  function showImportDialog() {
    if (importsState.batchRunning) return toast('图片正在上传，请等待上传完成', 'warning');
    $('#import-dialog').append($('#toast'));
    $('#import-dialog').showModal();
  }

  function initialize() {
    importsState.ocrBackend = document.body.dataset.defaultBackend;
    $('#choose-file').addEventListener('click', (event) => { event.stopPropagation(); fileInput.click(); });
    $('#choose-folder').addEventListener('click', (event) => { event.stopPropagation(); folderInput.click(); });
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => importFiles({files: [...fileInput.files], errors: []}, '批量图片识别'));
    folderInput.addEventListener('change', () => importFiles({files: [...folderInput.files], errors: []}, `文件夹：${folderInput.files[0]?.webkitRelativePath?.split('/')[0] || '批量导入'}`));
    ['dragenter', 'dragover'].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add('dragging'); }));
    ['dragleave', 'drop'].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove('dragging'); }));
    dropZone.addEventListener('drop', event => {
      if (importsState.batchRunning || importsState.importScanning) return toast('正在导入，请等待当前上传完成');
      importFiles(ReceiptImport.collectDrop(event.dataTransfer, count => { $('#folder-scan-status').textContent = `正在读取文件夹及子文件夹，已找到 ${count} 个文件…`; }), '拖拽批量识别');
    });

    const sampleNames = $$('.sample-button[data-sample]').map(button => button.dataset.sample);
    $$('.sample-button[data-sample]').forEach(button => button.addEventListener('click', () => startBatch([], [button.dataset.sample], `样单 ${button.dataset.sample}`)));
    $('#run-all-samples').addEventListener('click', () => startBatch([], sampleNames, `现有 ${sampleNames.length} 张标注样单端到端测试`));

    $('#bulk-retry').addEventListener('click', bulkRetry);

    window.addEventListener('beforeunload', event => {
      if (importsState.batchRunning || importsState.uploadingJobs?.size) { event.preventDefault(); event.returnValue = ''; }
    });

    const importSection = $('[data-page="import"]');
    $('#import-dialog [data-import-body]').append(importSection);
    importSection.removeAttribute('data-page');
    importSection.classList.remove('hidden');

    $$('a[href="#import"]').forEach(link => link.addEventListener('click', event => {
      event.preventDefault(); showImportDialog();
    }));
    $$('[data-dismiss-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
    $$('.workspace-dialog').forEach(dialog => {
      dialog.addEventListener('click', event => { if (event.target === dialog) {
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
      }});
      dialog.addEventListener('close', () => { document.body.classList.remove('modal-open'); if (dialog.contains($('#toast'))) document.body.append($('#toast')); });
    });

    $('#import-dialog').addEventListener('close', () => { fileInput.value = ''; folderInput.value = ''; });

  }

  return {initialize, importFiles, startBatch, runBatch, runPool, retryOne, bulkRetry, setBatchStep, showImportDialog};
}
