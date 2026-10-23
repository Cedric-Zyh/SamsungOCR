import {escapeHtml} from '../core/ui.mjs';
import {confirmInline} from '../core/inline_confirm.mjs';
import {ReceiptImport} from '../core/import_files.mjs';

export function createSampleLibrary({environment, ui, services, current, setBusy, refresh}) {
  const {$, $$, toast} = ui;
  let submitting = false, scanning = false, scanGeneration = 0, files = [];
  const dialog = () => $('#sample-import-dialog');

  function show() {
    if (current().busy) return;
    $('#sample-import-form').reset();
    files = [];
    $('#sample-import-company').value = current().company === '未分类' ? '' : current().company;
    $('#sample-import-companies').innerHTML = current().companies.filter(row => row.name !== '未分类')
      .map(row => `<option value="${escapeHtml(row.name)}"></option>`).join('');
    $('#sample-import-files-summary').textContent = '';
    $('#sample-import-error').textContent = '';
    $('#sample-import-submit').disabled = true;
    dialog().showModal();
  }

  async function submit(event) {
    event.preventDefault();
    if (submitting || scanning) return;
    if (!files.length || files.length > 100 || files.some(file => file.size > 20 * 1024 * 1024)
        || files.reduce((total, file) => total + file.size, 0) > 100 * 1024 * 1024) {
      $('#sample-import-error').textContent = '每次最多 100 张，单张不超过 20 MB，总大小不超过 100 MB。';
      return;
    }
    const body = new environment.window.FormData();
    files.forEach(file => body.append('files', file));
    body.append('company', $('#sample-import-company').value.trim());
    submitting = true; setBusy(true);
    const controls = $$('input,button', dialog());
    controls.forEach(control => { control.disabled = true; });
    $('#sample-import-submit').textContent = '正在导入…';
    $('#sample-import-error').textContent = '';
    try {
      const result = await services.samples.importFiles(body);
      dialog().close();
      const skipped = result.skipped?.length || 0;
      toast(`已导入 ${result.imported.length} 张样单${skipped ? `，跳过 ${skipped} 张重复编号样单` : ''}`, 'success');
      await refresh(result.imported.length ? result.company : undefined);
    } catch (error) { $('#sample-import-error').textContent = error.message; }
    finally {
      submitting = false; setBusy(false);
      controls.forEach(control => { control.disabled = false; });
      $('#sample-import-submit').textContent = '导入样单';
    }
  }

  async function select(selection) {
    if (submitting || scanning) return;
    scanning = true;
    const generation = ++scanGeneration;
    $('#sample-choose-file').disabled = $('#sample-choose-folder').disabled = true;
    $('#sample-import-submit').disabled = true;
    $('#sample-import-files-summary').textContent = '正在读取图片和子文件夹…';
    $('#sample-import-error').textContent = '';
    try {
      const found = await selection;
      if (generation !== scanGeneration) return;
      const {images, skipped} = ReceiptImport.summarize(found.files);
      files = images;
      $('#sample-import-files-summary').textContent = `已选 ${files.length} 张 · ${(files.reduce((sum, file) => sum + file.size, 0) / 1024 / 1024).toFixed(1)} MB${skipped ? `，跳过 ${skipped} 个非图片文件` : ''}`;
      if (found.errors.length) $('#sample-import-error').textContent = `${found.errors.length} 项读取失败：${found.errors[0].path}，可重新选择文件夹。`;
      else if (!files.length) $('#sample-import-error').textContent = '没有找到支持的图片，请重新选择。';
    } catch (error) {
      if (generation === scanGeneration) {
        files = [];
        $('#sample-import-files-summary').textContent = '';
        $('#sample-import-error').textContent = `读取失败：${error.message}`;
      }
    } finally {
      if (generation === scanGeneration) {
        scanning = false;
        $('#sample-choose-file').disabled = $('#sample-choose-folder').disabled = false;
        $('#sample-import-submit').disabled = !files.length;
      }
    }
  }

  async function remove() {
    const state = current(), names = [...state.selected];
    if (state.busy || !names.length) return;
    const accepted = await confirmInline({environment, anchor: $('#sample-delete-selected'),
      title: `删除 ${names.length} 张样单？`, confirmLabel: '确认删除',
      message: `将永久删除所选的 ${names.length} 张样单、原图、处理图片及历史测试记录，并释放样单编号。回单管理不受影响。`});
    if (!accepted) return;
    setBusy(true);
    try {
      const result = await services.samples.remove(names);
      toast(`已删除 ${result.deleted.length} 张样单`, 'success');
      await refresh();
    } catch (error) { toast(error.message, 'danger'); }
    finally { setBusy(false); }
  }

  function initialize() {
    $('#sample-import-open').addEventListener('click', show);
    $('#sample-delete-selected').addEventListener('click', remove);
    $('#sample-import-form').addEventListener('submit', submit);
    const fileInput = $('#sample-file-input'), folderInput = $('#sample-folder-input'), dropZone = $('#sample-drop-zone');
    $('#sample-choose-file').addEventListener('click', event => { event.stopPropagation(); fileInput.click(); });
    $('#sample-choose-folder').addEventListener('click', event => { event.stopPropagation(); folderInput.click(); });
    dropZone.addEventListener('click', event => {
      if (!submitting && !scanning && !event.target.closest('input,button')) fileInput.click();
    });
    [fileInput, folderInput].forEach(input => input.addEventListener('change', () => {
      select({files: [...input.files], errors: []}); input.value = '';
    }));
    ['dragenter', 'dragover'].forEach(name => dropZone.addEventListener(name, event => {
      event.preventDefault(); if (!submitting && !scanning) dropZone.classList.add('dragging');
    }));
    ['dragleave', 'drop'].forEach(name => dropZone.addEventListener(name, event => {
      event.preventDefault(); dropZone.classList.remove('dragging');
    }));
    dropZone.addEventListener('drop', event => {
      if (submitting || scanning) return;
      const generation = scanGeneration + 1;
      select(ReceiptImport.collectDrop(event.dataTransfer, count => {
        if (generation === scanGeneration) $('#sample-import-files-summary').textContent = `正在读取子文件夹，已找到 ${count} 个文件…`;
      }));
    });
    dialog().addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
    dialog().addEventListener('close', () => {
      scanGeneration++; scanning = false;
      $('#sample-choose-file').disabled = $('#sample-choose-folder').disabled = false;
      dropZone.classList.remove('dragging');
      $('#sample-import-open').focus({preventScroll: true});
    });
    environment.window.addEventListener('hashchange', () => { if (!submitting) dialog().close(); });
    environment.window.addEventListener('beforeunload', event => {
      if (submitting) { event.preventDefault(); event.returnValue = ''; }
    });
  }
  return {initialize};
}
