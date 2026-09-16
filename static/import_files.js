/* Directory entries are captured during the drop event, before awaiting reads. */
const ReceiptImport = (() => {
  const paths = new WeakMap();
  const isImage = file => /\.(jpe?g|png|bmp|webp)$/i.test(file.name || '');
  const relativePath = file => paths.get(file) || file.webkitRelativePath || file.name;
  const summarize = files => ({images: files.filter(isImage), skipped: files.filter(file => !isImage(file)).length});
  async function collectDrop(transfer, onProgress = () => {}) {
    const sources = Array.from(transfer.items || []).filter(item => item.kind === 'file').map(item => ({
      entry: item.webkitGetAsEntry?.(), file: item.getAsFile?.(),
    }));
    const fallback = Array.from(transfer.files || []);
    const files = [], errors = [];
    async function visit(entry, parent = '') {
      const path = parent + entry.name;
      try {
        if (entry.isFile) {
          const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
          paths.set(file, path); files.push(file); onProgress(files.length);
        } else if (entry.isDirectory) {
          const reader = entry.createReader();
          // Chromium returns directory children in batches; one read is not enough.
          while (true) {
            const children = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
            if (!children.length) break;
            for (const child of children) await visit(child, path + '/');
          }
        }
      } catch (error) { errors.push({path, message: error.message || '无法读取'}); }
    }
    if (sources.length) {
      for (const source of sources) {
        if (source.entry) await visit(source.entry);
        else if (source.file) files.push(source.file);
        else errors.push({path: '拖入的文件夹', message: '浏览器不支持拖入文件夹，请点击“选择文件夹”'});
      }
    } else files.push(...fallback);
    return {files, errors};
  }
  function batches(items) {
    const groups = [];
    for (let index = 0; index < items.length; index += 5000) groups.push(items.slice(index, index + 5000));
    return groups;
  }
  return {isImage, relativePath, summarize, collectDrop, batches};
})();
if (typeof module !== 'undefined') module.exports = ReceiptImport;
