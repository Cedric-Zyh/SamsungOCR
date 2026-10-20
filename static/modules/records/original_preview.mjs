import {createReviewImage} from '../review/review_image.mjs';
import {createReviewPreview} from '../review/review_preview.mjs';

/** Original-only viewing does not load a result or start a recognition job. */
export function createOriginalPreview({environment, ui}) {
  const {$, $$} = ui;
  const image = createReviewImage({environment, ui, reviewState: {}});
  const preview = createReviewPreview({environment, ui});
  let trigger = null;
  let jobId = '';
  const dialog = () => $('#original-preview-dialog');

  function open(item, anchor) {
    const modal = dialog();
    if (!modal || (!item.processing_job?.id && !item.sample_center)) return;
    $('#original-preview-title').textContent = item.sample_center ? '预览样单' : '预览回单';
    trigger = anchor;
    jobId = item.processing_job?.id || '';
    $('#original-preview-filename').textContent = item.filename || '回单图片';
    const content = $('#original-preview-content');
    // Reuse the existing viewer and its loading, retry, pan and zoom behavior.
    content.replaceChildren($('#review-template').content.querySelector('.review-preview').cloneNode(true));
    const picker = $('.image-source-picker', content);
    picker.replaceChildren();
    picker.textContent = '整页原图';
    const source = {label: '原图', url: item.sample_center ? item.preview_url : `/api/jobs/${encodeURIComponent(item.processing_job.id)}/image`};
    const show = retry => preview.show(source, {
      alt: item.filename || '回单原图', caption: '原图预览 · 拖动查看，按住 Ctrl / ⌘ 滚轮缩放', retry
    });
    image.attach(content);
    preview.attach(content, {retry: () => show(true), fallback: () => show(false)});
    if (!modal.open) modal.showModal();
    environment.document.body.classList.add('modal-open');
    image.reset();
    show(false);
  }

  function close() {
    if (dialog()?.open) {
      preview.cancel();
      dialog().close();
    }
  }

  function initialize() {
    const modal = dialog();
    if (!modal) return;
    modal.addEventListener('close', () => {
      // A delayed close event must not cancel a newly opened image.
      if (modal.open) return;
      preview.cancel();
      $('#original-preview-content').replaceChildren();
      if (!environment.document.querySelector('dialog[open]')) environment.document.body.classList.remove('modal-open');
      const focusTarget = trigger?.isConnected ? trigger
        : $$('[data-preview-job]').find(button => button.dataset.previewJob === jobId);
      focusTarget?.focus({preventScroll: true});
      trigger = null;
      jobId = '';
    });
    modal.addEventListener('click', event => {
      if (event.target.closest('[data-original-preview-close]') || event.target === modal) close();
    });
    environment.window.addEventListener('hashchange', close);
  }

  return {initialize, open, close};
}
