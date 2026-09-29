import {escapeHtml, planLabels} from '../core/ui.mjs';

export function renderRecognitionVariants(item) {
  const grouped = [];
  const dzt = [];
  for (const [stage, variants] of Object.entries(item.recognition_variants || {})) {
    for (const variant of variants || []) {
      if (variant.method === 'danzhengtong') dzt.push({stage, variant});
      else grouped.push({stage, variant});
    }
  }
  if (dzt.length) {
    const trace = dzt.map(x => x.variant.details?.danzhengtong).find(Boolean);
    grouped.push({dzt, trace});
  }
  return grouped.map(entry => {
    if (entry.dzt) {
      const simulated = entry.dzt.every(x => x.variant.details?.danzhengtong?.simulated);
      const stages = entry.dzt.map(({stage, variant}) => ({stage: planLabels[stage] || stage, value: variant.value, error: variant.error, details: Object.fromEntries(Object.entries(variant.details || {}).filter(([k]) => k !== 'danzhengtong'))}));
      return `<details class="variant-details"><summary>单证通${simulated ? '（模拟）' : ''}</summary><pre>${escapeHtml(JSON.stringify({danzhengtong: entry.trace, stages}, null, 2))}</pre></details>`;
    }
    const {stage, variant} = entry;
    return `<details class="variant-details"><summary>${escapeHtml(planLabels[stage] || stage)} · ${escapeHtml(variant.method)}${variant.error ? ' · 识别失败' : ''}</summary><pre>${escapeHtml(JSON.stringify(variant.details || variant.error, null, 2))}</pre></details>`;
  }).join('');
}
