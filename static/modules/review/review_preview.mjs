export function createReviewPreview({environment, ui, createImage = () => new environment.window.Image()}) {
  const {$, $$} = ui;
  let current = null, request = 0, pending = null;

  function cancel() {
    request++;
    if (pending) {
      pending.onload = null;
      pending.onerror = null;
      pending.removeAttribute?.('src');
      pending = null;
    }
  }

  function attach(content, {retry, fallback}) {
    cancel();
    current = {
      image: $('[data-preview]', content), viewport: $('[data-image-viewport]', content),
      feedback: $('[data-image-empty]', content), message: $('[data-image-message]', content),
      detail: $('[data-image-detail]', content), caption: $('[data-image-caption]', content),
      retry: $('[data-image-retry]', content), fallback: $('[data-image-fallback]', content),
      zoomControls: $$('[data-zoom]', content),
    };
    current.retry.addEventListener('click', retry);
    current.fallback.addEventListener('click', fallback);
  }

  function show(source, {caption = '', alt = '', canFallback = false, retry = false} = {}) {
    cancel();
    if (!current) return;
    const token = request, nodes = current;
    const render = (state, message, detail = '') => {
      nodes.viewport.dataset.imageState = state;
      nodes.viewport.setAttribute('aria-busy', String(state === 'loading'));
      nodes.image.classList.toggle('hidden', state !== 'ready');
      nodes.feedback.classList.toggle('hidden', state === 'ready');
      nodes.message.textContent = message;
      nodes.detail.textContent = detail;
      nodes.retry.classList.toggle('hidden', state !== 'error');
      nodes.fallback.classList.toggle('hidden', state !== 'error' || !canFallback);
      nodes.zoomControls.forEach(button => button.disabled = state !== 'ready');
    };
    nodes.caption.textContent = caption;
    nodes.image.removeAttribute('src');
    if (!source) {
      render('empty', '没有可用的预览图片', '可根据已识别信息继续核对，或稍后重新识别。');
      return;
    }
    render('loading', `正在加载${source.label}…`, '图片加载完成后即可拖动查看与缩放。');
    const loader = pending = createImage();
    let url = source.url;
    if (retry) {
      const [path, fragment] = url.split('#', 2);
      url = `${path}${path.includes('?') ? '&' : '?'}_preview_retry=${Date.now()}-${token}${fragment ? `#${fragment}` : ''}`;
    }
    const complete = success => {
      if (token !== request || nodes !== current) return;
      loader.onload = null;
      loader.onerror = null;
      pending = null;
      if (success) {
        nodes.image.src = url;
        nodes.image.alt = alt;
        render('ready', '');
      } else {
        render('error', `${source.label}加载失败`, canFallback
          ? '可以重新加载，或先查看整页回单继续核对。'
          : '可以重新加载，或从上方切换其他图片。');
      }
    };
    loader.onload = () => complete(loader.naturalWidth > 0);
    loader.onerror = () => complete(false);
    loader.src = url;
  }

  return {attach, show, cancel};
}
