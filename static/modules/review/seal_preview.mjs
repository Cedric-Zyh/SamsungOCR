import {escapeHtml} from '../core/ui.mjs';
import {sealImageUrl} from './seal_workspace.mjs';

/** Region images share the existing loader, pan and zoom. */
export function createSealPreview({environment, ui, showSource}) {
  let toolbar = null, cleanupLocation = null;
  function clear() {
    cleanupLocation?.(); cleanupLocation = null;
    toolbar?.remove(); toolbar = null;
  }
  function markLocation(content, box) {
    const viewport = ui.$('[data-image-viewport]', content), image = ui.$('[data-preview]', content);
    const marker = environment.document.createElement('div');
    marker.className = 'region-location-marker';
    marker.setAttribute('aria-label', '签章要求所在位置');
    marker.hidden = true;
    viewport.append(marker);
    let centered = false;
    const update = () => {
      if (image.classList.contains('hidden') || !image.naturalWidth) { marker.hidden = true; return; }
      marker.hidden = false;
      const width = image.clientWidth, height = image.clientHeight;
      const left = image.offsetLeft + box[0] * width, top = image.offsetTop + box[1] * height;
      Object.assign(marker.style, {left: `${left}px`, top: `${top}px`, width: `${box[2] * width}px`, height: `${box[3] * height}px`});
      if (!centered) { viewport.scrollTop = Math.max(0, top - viewport.clientHeight / 2); centered = true; }
    };
    image.addEventListener('load', update);
    const observer = environment.window?.ResizeObserver ? new environment.window.ResizeObserver(update) : null;
    observer?.observe(image);
    update();
    return () => { image.removeEventListener('load', update); observer?.disconnect(); marker.remove(); };
  }
  function show(detail) {
    clear();
    const content = ui.$('#review-content'), viewport = ui.$('[data-image-viewport]', content);
    if (!viewport) return;
    const original = sealImageUrl(detail.original), processed = sealImageUrl(detail.processed);
    const source = sealImageUrl(detail.url) || original || processed;
    if (!source) return;
    toolbar = environment.document.createElement('div');
    toolbar.className = 'seal-preview-toolbar';
    toolbar.innerHTML = `<strong>${escapeHtml(detail.label)}</strong><div role="group" aria-label="${escapeHtml(detail.label)}图片显示方式">${[
      ['original', '原图', original], ['processed', '处理后', processed],
    ].filter(([, , available]) => available).map(([key, label]) => `<button type="button" class="mini-button" data-seal-preview-mode="${key}" aria-pressed="false">${label}</button>`).join('')}</div><span data-seal-preview-label></span>`;
    viewport.before(toolbar);
    const display = (url, label, mode, location = null, preserveSelection = false) => {
      cleanupLocation?.(); cleanupLocation = null;
      toolbar.querySelector('[data-seal-preview-label]').textContent = label;
      if (!preserveSelection) content.querySelectorAll('[data-seal-evidence-url], [data-region-evidence-url]').forEach(button => button.setAttribute('aria-pressed', 'false'));
      toolbar.querySelectorAll('[data-seal-preview-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.sealPreviewMode === mode)));
      showSource({group: detail.group || 'seal', label: `${detail.label} · ${label}`, url});
      if (location) cleanupLocation = markLocation(content, location);
    };
    toolbar.querySelectorAll('[data-seal-preview-mode]').forEach(button => button.addEventListener('click', () => {
      const mode = button.dataset.sealPreviewMode;
      display(mode === 'processed' ? processed : original, button.textContent, mode);
    }));
    display(source, detail.evidenceLabel || (original ? '原图' : '处理后'), source === original ? 'original' : source === processed ? 'processed' : 'evidence', detail.location, detail.evidenceSelected);
  }
  return {show, clear};
}
