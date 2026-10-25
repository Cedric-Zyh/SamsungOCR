/** Keep sample browsing in the order captured by the source list. */
export function createSampleBrowse({ui, reviewState, openReview}) {
  const {$, toast} = ui;
  let ids = [], busy = false, request = 0;

  function sync() {
    const index = ids.indexOf(Number(reviewState.current?.id));
    const visible = reviewState.sampleView && index >= 0 && ids.length > 1;
    $('#review-dialog').classList.toggle('has-sample-browse', !!visible);
    for (const [selector, atEnd] of [
      ['#review-browse-prev', index <= 0],
      ['#review-browse-next', index < 0 || index >= ids.length - 1],
    ]) {
      const button = $(selector);
      button.classList.toggle('hidden', !visible);
      button.disabled = !visible || busy || atEnd;
    }
    const position = $('#review-browse-position');
    position.classList.toggle('hidden', !visible);
    position.textContent = visible ? `${index + 1} / ${ids.length}` : '';
    position.setAttribute('aria-label', visible ? `第 ${index + 1} 张，共 ${ids.length} 张样单` : '');
  }

  function setContext(values = []) {
    ids = [...new Set((Array.isArray(values) ? values : []).map(Number)
      .filter(id => Number.isSafeInteger(id) && id > 0))];
    sync();
  }

  function clear() {
    request++;
    busy = false;
    setContext();
  }

  async function move(step) {
    if (busy || !reviewState.sampleView) return;
    const index = ids.indexOf(Number(reviewState.current?.id)), id = ids[index + step];
    if (index < 0 || !id) return;
    const imageGroup = reviewState.reviewImages?.[reviewState.reviewImageIndex]?.group;
    const token = ++request;
    busy = true;
    sync();
    try {
      await openReview(id, {sample: true, browseIds: ids, imageGroup});
    } catch (error) {
      if (token === request) toast(`样单加载失败：${error.message}`, 'danger');
    } finally {
      if (token === request) { busy = false; sync(); }
    }
  }

  function initialize() {
    $('#review-browse-prev').addEventListener('click', () => move(-1));
    $('#review-browse-next').addEventListener('click', () => move(1));
    sync();
  }

  return {initialize, setContext, clear};
}
