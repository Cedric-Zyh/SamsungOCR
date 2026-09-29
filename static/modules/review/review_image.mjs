const MIN_ZOOM = .5, MAX_ZOOM = 4;
const STORAGE_KEY = 'receipt-review-preview-width';
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function createReviewImage({environment, ui, reviewState}) {
  const {$, $$} = ui;
  let current = null;

  function zoom(action, point) {
    if (!current || current.image.classList.contains('hidden')) return;
    const {viewport, image, label} = current;
    const before = reviewState.reviewZoom || 1;
    const next = action === 'fit' ? 1 : typeof action === 'number' ? clamp(action, MIN_ZOOM, MAX_ZOOM)
      : clamp(before + (action === 'in' ? .25 : -.25), MIN_ZOOM, MAX_ZOOM);
    const bounds = viewport.getBoundingClientRect();
    const imageBounds = image.getBoundingClientRect();
    const x = point?.x ?? bounds.left + viewport.clientWidth / 2;
    const y = point?.y ?? bounds.top + viewport.clientHeight / 2;
    // Preserve the image coordinate under the cursor (or viewport center).
    const imageX = x - imageBounds.left, imageY = y - imageBounds.top;
    const left = viewport.scrollLeft, top = viewport.scrollTop;
    reviewState.reviewZoom = next;
    image.style.width = `${next * 100}%`;
    label.textContent = `${Math.round(next * 100)}%`;
    if (action === 'fit') viewport.scrollTo(0, 0);
    else {
      viewport.scrollLeft = left + imageX * (next / before - 1);
      viewport.scrollTop = top + imageY * (next / before - 1);
    }
  }

  function reset() {
    if (!current) return;
    current.cancelPan?.();
    reviewState.reviewZoom = 1;
    current.image.style.width = '100%';
    current.label.textContent = '100%';
    current.viewport.scrollTo(0, 0);
  }

  function attach(content) {
    current?.cancelPan?.();
    const viewport = $('[data-image-viewport]', content), image = $('[data-preview]', content);
    current = {viewport, image, label: $('[data-zoom-label]', content)};
    image.draggable = false;
    image.addEventListener('dragstart', event => event.preventDefault());
    viewport.addEventListener('wheel', event => {
      if ((!event.ctrlKey && !event.metaKey) || image.classList.contains('hidden')) return;
      event.preventDefault();
      zoom((reviewState.reviewZoom || 1) * Math.exp(-event.deltaY * .002), {x: event.clientX, y: event.clientY});
    }, {passive: false});
    let drag = null;
    viewport.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.pointerType === 'touch' || image.classList.contains('hidden')) return;
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
      viewport.setPointerCapture(event.pointerId);
      viewport.classList.add('is-dragging');
      event.preventDefault();
    });
    viewport.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      viewport.scrollLeft = drag.left - (event.clientX - drag.x);
      viewport.scrollTop = drag.top - (event.clientY - drag.y);
    });
    const cancelPan = () => {
      if (!drag) return;
      const pointerId = drag.id;
      drag = null;
      viewport.classList.remove('is-dragging');
      if (viewport.hasPointerCapture(pointerId)) viewport.releasePointerCapture(pointerId);
    };
    current.cancelPan = cancelPan;
    const stopDrag = event => { if (drag?.id === event.pointerId) cancelPan(); };
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => viewport.addEventListener(type, stopDrag));
    $$('[data-zoom]', content).forEach(button => button.addEventListener('click', () => zoom(button.dataset.zoom)));
    setupDivider($('[data-review-divider]', content));
  }

  function setupDivider(divider) {
    if (!divider) return;
    const layout = divider.parentElement;
    divider.setAttribute('aria-valuemin', '32');
    divider.setAttribute('aria-valuemax', '64');
    let proportion = 48, drag = null;
    try {
      const saved = Number(environment.localStorage.getItem(STORAGE_KEY));
      if (saved >= 32 && saved <= 64) proportion = saved;
    } catch (_) { /* Storage may be unavailable in a private browser. */ }
    function render(save = false) {
      layout.style?.setProperty('--review-preview-width', `${proportion}%`);
      divider.setAttribute('aria-valuenow', String(Math.round(proportion)));
      divider.setAttribute('aria-valuetext', `图片占 ${Math.round(proportion)}%`);
      if (save) { try { environment.localStorage.setItem(STORAGE_KEY, String(proportion)); } catch (_) {} }
    }
    render();
    divider.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      drag = event.pointerId;
      divider.setPointerCapture(event.pointerId);
      divider.classList.add('is-dragging');
      event.preventDefault();
    });
    divider.addEventListener('pointermove', event => {
      if (drag !== event.pointerId) return;
      const bounds = layout.getBoundingClientRect();
      if (!bounds.width) return;
      proportion = clamp((event.clientX - bounds.left) / bounds.width * 100, 32, 64);
      render();
    });
    const stopDrag = event => {
      if (drag !== event.pointerId) return;
      drag = null;
      divider.classList.remove('is-dragging');
      if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
      render(true);
    };
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => divider.addEventListener(type, stopDrag));
    divider.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
      event.preventDefault();
      proportion = event.key === 'Home' ? 48 : clamp(proportion + (event.key === 'ArrowRight' ? 2 : -2), 32, 64);
      render(true);
    });
  }

  return {attach, reset, zoom};
}
