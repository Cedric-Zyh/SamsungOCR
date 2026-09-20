/* Presentation policy shared by the daily workbench and review workspace. */
const ReceiptWorkbench = (() => {
  function hasProviderFailure(record) {
    if (!record) return false;
    const providerPattern = /(单证通|danzhengtong)/i;
    const failurePattern = /(失败|超时|timeout|connectionerror|readtimeout|max retries|nodename|无法连接|连接失败)/i;
    const messages = [record.error_message, ...(record.review_reasons || [])]
      .filter(Boolean).map(String);
    if (messages.some(message => providerPattern.test(message) && failurePattern.test(message))) return true;
    return Object.values(record.recognition_variants || {}).some(variants =>
      (variants || []).some(variant => variant?.method === 'danzhengtong' && variant.error));
  }

  function category(item) {
    if (item.status === 'failed') return 'failed';
    if (['awaiting_upload', 'ready', 'queued', 'running'].includes(item.status)) return 'processing';
    if (item.status === 'cancelled') return 'cancelled';
    // A provider can fail one or more stages while the surrounding job is
    // still persisted as succeeded (for example, a remote upload failure).
    // Such records need retry handling, not a review action.
    if (hasProviderFailure(item.record || item)) return 'failed';
    const record = item.record || item;
    const humanDecision = ['确认通过', '确认不通过'].includes(item.review_status);
    if (!humanDecision && ['date_check', 'seal_check'].some(key => record[key]?.status === '不匹配')) return 'review';
    if (item.review_status === '待复核') return 'review';
    return item.final_result === '不通过' ? 'rejected' : item.final_result === '通过' ? 'passed' : 'review';
  }
  function matches(item, filter) {
    return filter === 'all' || category(item) === filter;
  }
  function ordered(items) {
    const rank = {review: 0, failed: 1, processing: 2, rejected: 3, passed: 4, cancelled: 5};
    const processingRank = {running: 0, queued: 1, awaiting_upload: 2, ready: 3};
    return [...items].sort((a, b) => {
      const categoryDifference = rank[category(a)] - rank[category(b)];
      if (categoryDifference) return categoryDifference;
      if (category(a) !== 'processing') return 0;
      return (processingRank[a.status] ?? 4) - (processingRank[b.status] ?? 4);
    });
  }
  function signatureCheck(record) {
    const saved = record.signature_check;
    if (saved) return saved;
    const fields = record.fields || {};
    const contact = String(fields['仓库联系人'] || record.internal_fields?.['仓库联系人'] || '').trim();
    const receiver = String(fields['仓库接收人'] || record.internal_fields?.['仓库接收人'] || '').trim();
    const normalize = value => value.replace(/\s+/g, '');
    const contactNames = contact.split(/[、,，/／;；|]+/).map(normalize).filter(Boolean);
    const receiverName = normalize(receiver);
    return {contact, receiver,
      status: contact && receiver ? (contactNames.includes(receiverName) ? '匹配' : '不匹配') : '未识别',
      reliable: !!(contact && receiver)};
  }
  function checkStatus(record, key) {
    const check = key === 'signature' ? signatureCheck(record) : record[`${key}_check`];
    if (!check) return '结果未加载';
    if (check.status === '匹配' && !check.reliable) return '匹配待确认';
    return check.status || '未识别';
  }
  function dateValue(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return '';
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value : '';
  }
  function dateReview(required, actual) {
    required = dateValue(required); actual = dateValue(actual);
    return {required, actual, choice: actual ? (actual === required ? 'same' : 'different') : '',
      draft: actual || required};
  }
  function chooseDate(model, choice, value) {
    if (choice === 'same') return {...model, choice, actual: model.required, draft: model.required};
    if (choice === 'different') return {...model, choice, actual: '', draft: model.required || model.draft};
    const actual = dateValue(value);
    return {...model, draft: value, actual: actual === model.required ? '' : actual};
  }
  function issues(record) {
    const found = [];
    for (const [key, title] of [['date', '签收日期'], ['seal', '客户印章']]) {
      const check = record[`${key}_check`] || {};
      if (check.status === '不匹配') found.push({target: key, message: `${title}与要求不一致`});
      else if (check.status === '部分匹配') found.push({target: key, message: check.message || `${title}部分文字缺失，需人工复核`});
      else if (check.status === '部分识别') found.push({target: key, message: check.message || `${title}仅识别出部分内容，需人工复核`});
      else if (!check.reliable) found.push({target: key, message: check.status === '未执行' ? `${title}未执行核验` : check.status === '匹配' ? `${title}比对匹配，但证据不足，仍需确认` : `${title}尚未可靠识别`});
    }
    const ignoreLowConfidence = record.recognition_config?.acceptance?.low_confidence_mode === 'ignore';
    for (const reason of record.review_reasons || []) {
      if (ignoreLowConfidence && /低置信度/.test(reason)) continue;
      let target = /日期|到货/.test(reason) ? 'date' : /印章|签章/.test(reason) ? 'seal' : /商品/.test(reason) ? 'products' : /签名|填写/.test(reason) ? 'handwriting' : /字段/.test(reason) ? 'fields' : 'evidence';
      const lowFields = Object.entries(record.field_metadata || {}).filter(([,meta]) => meta.low_confidence).map(([name]) => name);
      if (target === 'fields' && lowFields.length && lowFields.every(name => name === '仓库接收人')) target = 'handwriting';
      if (!found.some(item => item.target === target)) found.push({target, message: reason});
    }
    return found;
  }
  function imageSources(record) {
    const sources = [], seen = new Set();
    function add(url, label, group) {
      if (typeof url !== 'string' || !url.startsWith('/files/') || seen.has(url)) return;
      seen.add(url); sources.push({url, label, group});
    }
    add(record.preview_url, '整页回单', 'page');
    for (const [key, group, label] of [['date', 'date', '日期区域'], ['seals', 'seal', '印章区域']]) {
      if (key === 'seals' && ['qingtong_template_and_ocr', 'qingtong_any_channel', 'qingtong_all_channel'].includes(record.seal_check?.dual_check?.policy)) {
        const sealArtifacts = record.processing_artifacts?.seals || [];
        const selectedIndex = record.seal_check.dual_check.selected?.index;
        const selectedArtifact = sealArtifacts.find(item =>
          item.api_index === selectedIndex || item.index === selectedIndex
        ) || (Number.isInteger(selectedIndex) ? sealArtifacts[selectedIndex] : null);
        const oriented = selectedArtifact?.color_isolated_oriented_url ||
          sealArtifacts.find(item => item.color_isolated_oriented_url)?.color_isolated_oriented_url;
        if (oriented) add(oriented, '印章区域 · 按章型文字旋正', 'seal');
        const corrected = !oriented && sealArtifacts.find(item =>
          item.orientation?.applied_rotation === 180 && item.orientation_corrected_url);
        if (corrected) add(corrected.orientation_corrected_url, '印章区域 · 已自动旋转 180°', 'seal');
        const box = record.seal_check.dual_check.selected?.xyxy;
        if (!corrected && record.seal_check.api?.ok !== false && Number.isSafeInteger(record.id) && record.id > 0
            && Array.isArray(box) && box.length === 4 && box.every(value => typeof value === 'number' && Number.isFinite(value))
            && box[2] > box[0] && box[3] > box[1]) {
          const revision = typeof record.review_revision === 'string' ? `?revision=${encodeURIComponent(record.review_revision)}` : '';
          add(`/files/selected-seal/${record.id}.png${revision}`, '印章区域', 'seal');
        }
        // Local candidates may be different stamps from QingTong's decision.
        continue;
      }
      let index = 0;
      const items = key === 'date'
        ? (record.processing_artifacts?.[key] || []).filter(item => item.variant === '紧凑区域')
        : (record.processing_artifacts?.[key] || []);
      for (const item of items) {
        // Round-stamp OCR is performed on the color-safe image after the
        // detected stamp-type line has been deskewed.  Make that same image
        // the primary seal preview; the unrotated crop remains available in
        // the evidence panel for audit.
        const oriented = key === 'seals' && item.color_isolated_oriented_url;
        const corrected = item.orientation?.applied_rotation === 180
          ? item.orientation_corrected_url || item.original_url : item.original_url;
        const url = oriented || corrected;
        if (url && !seen.has(url)) add(url, oriented
          ? `${label} ${++index} · 按章型文字旋正`
          : item.orientation?.applied_rotation === 180
            ? `${label} ${++index} · 已自动旋转 180°` : `${label} ${++index}`, group);
      }
    }
    return sources;
  }
  function nextReview(rows, currentId, deferred) {
    return rows.find(row => row.id !== currentId && !deferred.has(row.id));
  }
  return {hasProviderFailure, category, matches, ordered, signatureCheck, checkStatus, dateReview, chooseDate, issues, imageSources, nextReview};
})();
if (typeof module !== 'undefined') module.exports = ReceiptWorkbench;
