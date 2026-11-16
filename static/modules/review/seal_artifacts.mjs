import {isDanzhengtongSealArtifact} from './seal_component_fields.mjs';
import {mergeSealArtifacts, sealImageUrl} from './seal_workspace.mjs';

const objects = value => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : [];
const text = value => typeof value === 'string' ? value.trim() : '';
const image = (...urls) => urls.map(sealImageUrl).find(Boolean) || '';
const isRemoteCheck = check => check?.recognition_mode === 'danzhengtong'
  || [check?.source, check?.backend].some(value => text(value).includes('单证通'));

function uploadedPage(record) {
  const source = record.sample_center && text(record.filename)
    ? `/files/samples/original/${encodeURIComponent(record.filename)}`
    : text(record.stored_name) ? `/files/uploads/${encodeURIComponent(record.stored_name)}` : '';
  return image(record.original_url, source, record.preview_url);
}

/** Project saved provider results into the workspace without changing OCR evidence. */
export function sealWorkspaceArtifacts(record = {}) {
  record ||= {};
  const variants = objects(record.recognition_variants?.seal).filter(row => row.method === 'danzhengtong');
  const entries = variants.length ? variants : isRemoteCheck(record.seal_check)
    ? [{details: {seal_check: record.seal_check}}] : [];
  const items = mergeSealArtifacts([
    ...objects(record.processing_artifacts?.seals),
    ...entries.flatMap(entry => objects(entry.details?.processing_artifacts?.seals)
      .map(item => ({...item, provider: 'danzhengtong'}))),
  ]);
  const page = uploadedPage(record);
  const checks = entries.map(entry => entry.details?.seal_check || {});
  if (items.some(isDanzhengtongSealArtifact)) {
    return items.map(item => {
      if (!isDanzhengtongSealArtifact(item)) return item;
      const check = checks.find(check => text(check.recognized || check.display_text) === text(item.provider_text));
      const upload = image(item.input_url, item.color_isolated_url, item.original_url);
      return {...item, ...(check ? {provider_status: check.status} : {}),
        ...(!upload && page ? {input_url: page, input_label: '上传图（整页回单）', fallback_upload: true} : {})};
    });
  }
  for (const entry of entries) {
    const check = entry.details?.seal_check || {};
    const values = Array.isArray(entry.value) ? entry.value.filter(value => typeof value === 'string') : [];
    items.push({provider: 'danzhengtong', index: 0,
      provider_text: entry.error ? '' : text(check.recognized || check.display_text) || values.join('；'),
      provider_status: entry.error ? '识别失败' : check.status,
      provider_color: text(check.provider_color), provider_seal_type: text(check.provider_seal_type),
      input_url: image(entry.details?.input_url, check.input_url) || page,
      input_label: image(entry.details?.input_url, check.input_url) ? '上传图' : '上传图（整页回单）',
      returned_image_status: 'unavailable', fallback_upload: true});
  }
  return items;
}
