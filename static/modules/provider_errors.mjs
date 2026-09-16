/** Read saved failures, including records created before detailed error capture. */
export function providerErrors(record) {
  const errors = new Set();
  for (const variants of Object.values(record.recognition_variants || {})) {
    for (const variant of variants || []) {
      if (variant.method === 'danzhengtong' && variant.error) errors.add(String(variant.error));
    }
  }
  for (const reason of record.review_reasons || []) {
    const text = String(reason);
    const match = text.match(/(?:danzhengtong|单证通)[：:]\s*(.*)/i);
    if (match?.[1]) errors.add(match[1]);
    else if (/(?:danzhengtong|单证通)/i.test(text)) errors.add(text);
  }
  if (!errors.size && /单证通/.test(record.error_message || '')) errors.add(record.error_message);
  return [...errors];
}

export function providerErrorIssues(record) {
  return providerErrors(record).map(message => ({target: 'evidence', message, danger: true}));
}
