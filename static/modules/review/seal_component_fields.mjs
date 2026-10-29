// Display adapters for saved seal schemas. These never decide a match or rebuild an identity.
const rows = value => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : [];
const strings = value => Array.isArray(value) ? value.filter(text => typeof text === 'string') : [];
const text = value => typeof value === 'string' ? value.trim() : '';
const compact = value => text(value).replace(/\s+/gu, '');
const number = '(?:[①-⑳]|\\([0-9０-９]{1,3}\\)|（[0-9０-９]{1,3}）|[0-9０-９]{1,3})';
const purpose = new RegExp(`^(.{1,18}(?:专用章|收货章|业务受理章))(${number})?$`, 'u');
const numberOnly = new RegExp(`^${number}$`, 'u');

export function rgbSealPreview(url) {
  if (typeof url !== 'string' || !/^\/files\/(?:samples\/)?artifacts\//u.test(url)) return url || '';
  if (/[?&]preview=rgb(?:&|$)/u.test(url)) return url;
  const [path, fragment] = url.split('#', 2);
  return `${path}${path.includes('?') ? '&' : '?'}preview=rgb${fragment ? `#${fragment}` : ''}`;
}

function activeReads(item) {
  const inputs = rows(item.inputs);
  return rows(item.reads).filter(read => {
    const input = inputs.find(input => input.id && input.id === read.input_id);
    return !read.simulated && !read.error && read.status !== 'failed' && !read.superseded_by
      && !input?.superseded_by && !input?.simulated && input?.status !== 'failed'
      && !/diagnostic|comparison/u.test(read.channel || '') && !/仅供核对|原分段/u.test(read.zone_label || '');
  }).map(read => ({...read, input: inputs.find(input => input.id && input.id === read.input_id)}));
}

export function savedSealText(item) {
  return ['combined_text', 'rectangular_text', 'same_region_reconstructed_text',
    'round_type_band_reconstructed_text', 'unwrapped_text', 'isolated_text',
    'color_isolated_oriented_text', 'color_isolated_text', 'secondary_read_text']
    .map(key => text(item[key])).find(Boolean) || '';
}

function rectangularFields(item) {
  const reads = activeReads(item);
  const recorded = strings(item.rectangular_row_texts);
  const fieldRows = rows(item.rectangular_form_fields);
  const formIndexes = new Set(fieldRows.map(row => row.rectangle_row_index ?? row.row_index));
  const inputs = rows(item.inputs).filter(input => input.channel === 'body' && Number.isInteger(input.rectangle_row_index));
  const indexes = [...new Set([...recorded.map((_, i) => i), ...inputs.map(input => input.rectangle_row_index)])].sort((a, b) => a - b);
  const fields = [];
  for (const index of indexes) {
    if (formIndexes.has(index)) continue;
    const input = inputs.find(input => input.rectangle_row_index === index);
    const observations = reads.filter(read => read.rectangle_row_index === index
      && !['empty_receiving_field', 'receiving_form_field'].includes(read.rectangle_field_kind));
    const values = [...new Set(observations.map(read => text(read.text)).filter(Boolean))];
    const value = text(recorded[index]) || values.join(' / ');
    const url = input?.image_url || item.rectangular_row_urls?.[index] || observations[0]?.image_url || '';
    if (!value && !url) continue;
    const part = compact(value).match(purpose);
    if (part) {
      fields.push({label: '印章用途', value: part[1], url});
      if (part[2]) fields.push({label: '印章编号', value: part[2], url});
      continue;
    }
    const label = numberOnly.test(compact(value)) ? '印章编号'
      : /(?:电话|TEL[:：]|Tel[:：])/u.test(value) ? '联系电话'
      : /站代码|站点|\d{4,12}站/u.test(value) ? '站点代码'
      : /(?:地址|路\d|号.*室)/u.test(value) ? '地址'
      : /DC\s*\d|\dDC[A-Z]?/iu.test(value) ? '仓库与代码'
      : /公司|服务中心/u.test(value) ? '单位名称'
      : `${item.rectangular_layout === 'frame_columns' ? '文字块' : '文字行'} ${index + 1}`;
    fields.push({label, value, url, conflict: !recorded[index] && values.length > 1 ? values.join(' / ') : ''});
  }
  const formFields = fieldRows.map(row => ({
    label: /^日期/u.test(text(row.text)) ? '日期' : /收货人|签收人|接收人/u.test(text(row.text)) ? '收货人' : '填写内容',
    value: text(row.text), url: row.image_url || item.rectangular_row_urls?.[row.rectangle_row_index ?? row.row_index] || '',
  }));
  return {fields, formFields};
}

export function savedSealComponents(item) {
  if (item.shape === '矩形' || item.rectangular_layout) {
    const rectangular = rectangularFields(item);
    if (rectangular.fields.length || rectangular.formFields.length) return rectangular;
  }
  const observations = activeReads(item), used = new Set(), fields = [];
  const definitions = [
    ['单位名称', 'unwrapped_text', 'unwrapped_url', /环形公司名称|上弧名称|下弧公司名称/u, true],
    ['章内部门', 'department_text', 'department_url', /章内部门/u],
    ['印章用途', 'round_type_band_text', 'round_type_band_url', /章型文字/u],
    ['印章编号', 'stamp_number_text', 'stamp_number_url', /章内编号/u],
    ['站点代码', 'station_code_text', '', /上弧站代码/u],
    ['站点编号', 'station_text', 'station_url', /站点编号/u],
    ['联系电话', 'phone_text', 'phone_url', /电话/u],
    ['网址', 'website_text', 'website_url', /网址/u, true],
    ['备案编号', 'ring_serial_text', 'ring_serial_url', /备案编号/u, true],
  ];
  for (const [label, property, imageProperty, pattern, rgb] of definitions) {
    const candidates = observations.filter(read => pattern.test(read.zone_label || '')
      || (property === 'unwrapped_text' && read.channel === 'ring'
        && (read.input?.zone_name === 'company' || (item.unwrapped_url && (read.image_url || read.input?.image_url) === item.unwrapped_url))));
    const saved = text(item[property]);
    const values = [...new Set(candidates.map(read => text(read.text)).filter(Boolean))];
    const selectedRead = candidates.find(read => compact(read.text) === compact(saved)) || candidates[0];
    const rawUrl = item[imageProperty] || selectedRead?.image_url || selectedRead?.input?.image_url || '';
    if (!saved && !candidates.length && !rawUrl) continue;
    candidates.forEach(read => used.add(read));
    fields.push({label, value: saved || values.join(' / '), url: rgb ? rgbSealPreview(rawUrl) : rawUrl,
      conflict: !saved && values.length > 1 ? values.join(' / ') : '',
      note: !saved && candidates.some(read => /不完整|待核对/u.test(read.zone_label || '')) ? '读数待核对' : ''});
  }
  const purposeField = fields.find(field => field.label === '印章用途');
  const purposeParts = compact(purposeField?.value).match(purpose);
  if (purposeParts?.[2]) {
    purposeField.value = purposeParts[1];
    const numberField = fields.find(field => field.label === '印章编号');
    if (!numberField) fields.splice(fields.indexOf(purposeField) + 1, 0,
      {label: '印章编号', value: purposeParts[2], url: purposeField.url});
    else if (compact(numberField.value) !== purposeParts[2]) numberField.conflict = `${numberField.value} / ${purposeParts[2]}`;
  }
  // Unknown fields stay visible using their actual region labels, rather than being silently discarded.
  const groups = new Map();
  for (const read of observations) {
    if (used.has(read) || !['ring', 'layout_inner', 'layout_lower', 'body'].includes(read.channel)) continue;
    const key = read.input_id || read.image_url;
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(read);
  }
  for (const members of groups.values()) {
    const first = members[0], values = [...new Set(members.map(read => text(read.text)).filter(Boolean))];
    const label = first.zone_label || (first.channel === 'ring' ? '环形文字' : first.channel === 'layout_lower' ? '下弧文字' : '其他文字');
    const url = first.image_url || first.input?.image_url || '';
    fields.push({label, value: values.join(' / '), url: first.channel === 'ring' ? rgbSealPreview(url) : url,
      conflict: values.length > 1 ? values.join(' / ') : ''});
  }
  if (!fields.length && savedSealText(item)) fields.push({label: '印章文字', value: savedSealText(item),
    url: item.color_isolated_oriented_url || item.color_isolated_url || item.original_url || ''});
  return {fields, formFields: []};
}

export function sealProcessingSteps(item) {
  const seen = new Set();
  return [
    ['提取印章颜色', item.color_isolated_url || item.isolated_url],
    ['方向校正', item.color_isolated_oriented_url],
    ['文字分区', item.multi_zone_layout?.overlay_url || item.double_border_layout?.overlay_url || item.rectangular_layout_url],
    ['外圈文字处理', item.ring_input_url],
    ['环形文字展开', rgbSealPreview(item.unwrapped_url)],
    ['编号区域', item.stamp_number_url || item.code_line_url],
  ].filter(([, url]) => url && !seen.has(url) && seen.add(url)).map(([label, url]) => ({label, url}));
}
