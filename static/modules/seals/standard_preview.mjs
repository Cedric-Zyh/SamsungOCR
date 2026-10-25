const MIN_ZOOM = 1;
const MAX_ZOOM = 4;
const ZOOM_STEP = 1.25;

/** Keep zoom in the detail pane, without distorting the stamp's aspect ratio. */
export function bindStandardPreview(detail) {
  const viewport = detail.querySelector('.seal-preview-viewport');
  const image = detail.querySelector('.seal-preview-image');
  const label = detail.querySelector('[data-seal-zoom-label]');
  const controls = [...detail.querySelectorAll('[data-seal-zoom]')];
  if (!viewport || !image || !label) return () => {};

  let zoom = MIN_ZOOM;
  let width = viewport.clientWidth;
  let height = viewport.clientHeight;
  const bindings = [];

  function render(next, reset = false) {
    // Preserve the point at the center while the image expands around it.
    const previous = zoom;
    const centerX = (viewport.scrollLeft + width / 2) / previous;
    const centerY = (viewport.scrollTop + height / 2) / previous;
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    width = viewport.clientWidth;
    height = viewport.clientHeight;
    image.style.width = zoom === MIN_ZOOM ? '100%' : `${width * zoom}px`;
    image.style.height = zoom === MIN_ZOOM ? '100%' : `${height * zoom}px`;
    viewport.scrollLeft = reset || zoom === MIN_ZOOM ? 0 : Math.max(0, centerX * zoom - width / 2);
    viewport.scrollTop = reset || zoom === MIN_ZOOM ? 0 : Math.max(0, centerY * zoom - height / 2);
    label.textContent = `${Math.round(zoom * 100)}%`;
    controls.forEach(button => {
      button.disabled = button.dataset.sealZoom === 'out' && zoom <= MIN_ZOOM
        || button.dataset.sealZoom === 'in' && zoom >= MAX_ZOOM;
    });
  }

  controls.forEach(button => {
    const listener = () => {
      if (button.disabled) return;
      const action = button.dataset.sealZoom;
      if (action === 'fit') render(MIN_ZOOM, true);
      else if (action === 'in') render(zoom * ZOOM_STEP);
      else if (action === 'out') render(zoom / ZOOM_STEP);
    };
    button.addEventListener('click', listener);
    bindings.push([button, listener]);
  });
  const ResizeObserver = detail.ownerDocument?.defaultView?.ResizeObserver;
  const observer = ResizeObserver ? new ResizeObserver(() => {
    if (viewport.clientWidth !== width || viewport.clientHeight !== height) render(zoom);
  }) : null;
  observer?.observe(viewport);
  render(MIN_ZOOM, true);

  return () => {
    observer?.disconnect();
    bindings.forEach(([button, listener]) => button.removeEventListener('click', listener));
  };
}
