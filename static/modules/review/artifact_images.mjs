/** Evidence images stay inside the review window, including desktop WebViews. */
const isTextStrip = image => image.naturalHeight > 0 && image.naturalWidth / image.naturalHeight >= 4;

export function bindArtifactImage({image, host, message, retry, setTimer = setTimeout, clearTimer = clearTimeout}) {
  const source = image.getAttribute('src');
  let timer;
  let attempt = 0;
  function state(value) {
    clearTimer(timer);
    host.dataset.imageState = value;
    host.setAttribute('aria-busy', String(value === 'loading'));
    message.textContent = value === 'loading' ? '正在加载图片…' : value === 'error' ? '图片未能显示，请重新加载' : '';
    message.hidden = value === 'ready';
    retry.hidden = value !== 'error';
  }
  function start() {
    state('loading');
    timer = setTimer(() => state('error'), 15000);
  }
  const loaded = () => {
    host.dataset.imageShape = isTextStrip(image) ? 'strip' : 'standard';
    state(image.naturalWidth > 0 ? 'ready' : 'error');
  };
  const failed = () => state('error');
  function reload() {
    start();
    const [path, fragment] = source.split('#', 2);
    image.src = `${path}${path.includes('?') ? '&' : '?'}_image_retry=${Date.now()}-${++attempt}${fragment ? `#${fragment}` : ''}`;
  }
  image.addEventListener('load', loaded);
  image.addEventListener('error', failed);
  retry.addEventListener('click', reload);
  start();
  if (image.complete) loaded();
  return () => {
    clearTimer(timer);
    image.removeEventListener('load', loaded);
    image.removeEventListener('error', failed);
    retry.removeEventListener('click', reload);
  };
}

export function createArtifactImages({document, setTimeout, clearTimeout}) {
  let cleanups = [];
  let dismiss = null;
  const binding = nodes => bindArtifactImage({...nodes, setTimer: setTimeout, clearTimer: clearTimeout});

  function open(link) {
    dismiss?.();
    const dialog = document.createElement('dialog');
    dialog.className = 'artifact-image-dialog';
    dialog.setAttribute('aria-label', '处理图片放大查看');
    dialog.innerHTML = `<header><h2></h2><div class="artifact-image-tools">
      <div class="artifact-image-size"><button type="button" data-image-fit aria-pressed="true">适应窗口</button><button type="button" data-image-size aria-pressed="false">原始尺寸</button></div>
      <button type="button" data-image-close aria-label="关闭图片预览" autofocus>×</button></div></header>
      <div class="artifact-image-stage"><img alt=""><p data-image-message role="status"></p><button type="button" data-image-retry hidden>重新加载</button></div>
      <footer><span data-image-hint>点击图片切换尺寸</span><span>Esc 关闭</span></footer>`;
    const title = link.querySelector('img').alt;
    dialog.querySelector('h2').textContent = title.replace(/（.*?）/g, '').trim();
    dialog.querySelector('h2').title = title;
    const image = dialog.querySelector('img');
    image.alt = link.querySelector('img').alt;
    image.src = link.querySelector('img').src;
    const stage = dialog.querySelector('.artifact-image-stage');
    const cleanup = binding({image, host: stage, message: dialog.querySelector('[data-image-message]'), retry: dialog.querySelector('[data-image-retry]')});
    const size = dialog.querySelector('[data-image-size]');
    const fit = dialog.querySelector('[data-image-fit]');
    if (isTextStrip(link.querySelector('img'))) {
      fit.textContent = '清晰查看';
      dialog.querySelector('[data-image-hint]').textContent = '左右滚动查看完整文字 · 点击图片切换尺寸';
    }
    function setOriginal(original) {
      stage.classList.toggle('original-size', original);
      size.setAttribute('aria-pressed', String(original));
      fit.setAttribute('aria-pressed', String(!original));
    }
    size.addEventListener('click', () => setOriginal(true));
    fit.addEventListener('click', () => setOriginal(false));
    image.addEventListener('click', () => setOriginal(!stage.classList.contains('original-size')));
    dismiss = () => {
      cleanup();
      dialog.close();
      dialog.remove();
      dismiss = null;
      if (link.isConnected) link.focus();
    };
    dialog.querySelector('[data-image-close]').addEventListener('click', () => dismiss?.());
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss?.(); });
    // Review keyboard shortcuts must not submit or zoom the underlying receipt.
    dialog.addEventListener('keydown', event => event.stopPropagation());
    document.body.append(dialog);
    dialog.showModal();
  }

  function attach(root) {
    cleanups.forEach(cleanup => cleanup());
    cleanups = [];
    dismiss?.();
    if (!document || !root?.querySelectorAll) return;
    for (const host of root.querySelectorAll('[data-artifact-image]')) {
      const link = host.querySelector('[data-artifact-open]');
      cleanups.push(binding({host, image: link.querySelector('img'), message: host.querySelector('[data-image-message]'), retry: host.querySelector('[data-image-retry]')}));
      const clicked = event => { event.preventDefault(); open(link); };
      link.addEventListener('click', clicked);
      cleanups.push(() => link.removeEventListener('click', clicked));
      const scrollStrip = event => {
        if (host.dataset.imageShape !== 'strip' || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        link.scrollBy({left: (event.key === 'ArrowRight' ? 1 : -1) * link.clientWidth * 0.7});
      };
      link.addEventListener('keydown', scrollStrip);
      cleanups.push(() => link.removeEventListener('keydown', scrollStrip));
    }
  }
  return {attach};
}
