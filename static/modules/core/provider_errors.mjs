/** Read provider failures from evidence, independent of localized wording. */
export function providerErrors(record) {
  const errors = new Set();
  for (const variants of Object.values(record.recognition_variants || {})) {
    for (const variant of variants || []) {
      if (variant.method === 'danzhengtong' && variant.error) errors.add(String(variant.error));
    }
  }
  if (!errors.size) {
    for (const issue of record.review_issues || []) {
      if (issue.code === 'provider_failure' && issue.provider === 'danzhengtong') {
        errors.add(String(issue.message));
      }
    }
  }
  return [...errors];
}

export function providerErrorIssues(record) {
  return providerErrors(record).map(message => ({target: 'evidence', message, danger: true}));
}
