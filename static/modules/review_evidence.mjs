import {percent, escapeHtml} from './ui.mjs';

export function createReviewEvidence({environment, ui, reviewState, api, fieldSchema}) {
  const {history} = environment;
  const {$} = ui;
  let activeArtifactItem = null;

  function fieldEditor(name, value, meta = {}) {
    const confidence = Number(meta.confidence ?? 0);
    const evidenceLabel = meta.pending ? '待单证通提供' : meta.confidence == null ? `${meta.source || 'OCR'} · 暂无置信度记录` : `${percent(confidence)} · ${meta.source || 'OCR'}`;
    const original = String(meta.original ?? '');
    const correctionNote = original && original !== String(value)
      ? `<small class="original-ocr">原始 OCR：${escapeHtml(original)}</small>` : '';
    return `<label class="field-editor ${meta.low_confidence ? 'low-confidence' : ''}"><span>${escapeHtml(name)}<em>${escapeHtml(evidenceLabel)}</em></span><textarea ${meta.pending ? 'readonly' : ''} name="field:${escapeHtml(name)}" rows="${String(value).length > 45 ? 3 : 1}">${escapeHtml(value)}</textarea>${correctionNote}${meta.low_confidence ? '<small>低置信度，请人工检查</small>' : ''}</label>`;
  }

  function renderProductTable(table, root) {
    const section = $('[data-product-section]', root), target = $('[data-product-table]', root);
    if (!table?.rows?.length) { target.innerHTML = `<p class="empty-state">${table?.status === '未执行' ? '本次未开启商品明细识别' : table?.status === '识别失败' ? '商品明细识别失败' : '本回单没有识别到商品明细'}</p>`; return; }
    section.classList.remove('hidden');
    const columns = table.columns || [];
    target.innerHTML = `<table class="product-review-table"><thead><tr>${columns.map(name => `<th>${escapeHtml(name)}</th>`).join('')}<th>行置信度</th></tr></thead><tbody>${table.rows.map((row, rowIndex) => `<tr>${columns.map(name => {
      const value = row.values?.[name] || '', confidence = Number(row.confidences?.[name] || 0);
      const low = (row.low_confidence_columns || []).includes(name);
      const source = row.sources?.[name] || row.source || 'OCR';
      return `<td class="${low ? 'low-confidence-cell' : ''}"><input class="product-cell-input" data-product-row="${rowIndex}" data-product-column="${escapeHtml(name)}" value="${escapeHtml(value)}"><small>${value ? percent(confidence) : '未识别'} · ${escapeHtml(source)}</small></td>`;
    }).join('')}<td class="row-confidence">${percent(row.row_confidence)}</td></tr>`).join('')}</tbody></table><p class="product-table-note">表格综合置信度 ${percent(table.confidence)} · ${escapeHtml(table.source || '')}</p>`;
  }

  function renderArtifacts() {
    const target = $('[data-artifacts]', $('#review-content'));
    let artifacts = reviewState.current?.processing_artifacts?.[reviewState.artifactTab] || [];
    // Date recognition is currently restricted to the compact cell.  Filter
    // legacy records as well, so previously saved wide/lower artifacts do not
    // reappear in the review UI.
    if (reviewState.artifactTab === 'date') {
      artifacts = artifacts.filter(item => item.variant === '紧凑区域');
    }
    if (reviewState.artifactTab === 'signature_requirement') {
      target.innerHTML = artifacts.length
        ? artifacts.map(item => `<article class="artifact-group"><h4>签章要求行 <small>${escapeHtml(item.backend || '')} · 二次识别 OCR：${escapeHtml(item.error ? `识别失败：${item.error}` : item.ocr_text || '未识别')}</small></h4><div class="artifact-grid">${artifactCard('签章要求行原图', item.original_url)}${artifactCard('签章要求行去印章色图（识别输入）', item.color_clean_url)}</div></article>`).join('')
        : '<div class="empty-state">没有保存的签章要求区域（旧记录可重新识别生成）</div>';
      return;
    }
    if (!artifacts.length) { target.innerHTML = '<div class="empty-state">没有保存的中间处理图（旧记录可重新识别生成）</div>'; return; }
    if (reviewState.artifactTab === 'date') {
      target.innerHTML = artifacts.map(item => {
        activeArtifactItem = item;
        const textsFor = (variants, labels) => (variants || [])
          .filter(row => !labels || labels.includes(row.preprocessing))
          .flatMap(row => row.ocr_texts || [])
          .filter(Boolean)
          .join(' | ');
        const cropText = labels => textsFor(item.ocr_variants, labels);
        const lineText = labels => textsFor(item.date_line_ocr_variants, labels);
        const lineWhiteInfo = item.date_line_white_candidate
          ? `<small>原色白底候选（历史证据）：${escapeHtml(item.date_line_white_candidate)}</small><small class="low-note">${escapeHtml(item.date_line_white_acceptance_note || '')}</small>`
          : '';
        const farLowerInfo = item.far_lower_server_backend
          ? `<small>远下方跨几何复核：Mobile 完整区域 + ${escapeHtml(item.far_lower_server_backend)} 窄日期行${item.far_lower_cross_model_mode === 'right_padding_complementary' ? ' + 右侧补白互补证据' : ''}${item.far_lower_cross_model_candidate ? ` · 可靠候选 ${escapeHtml(item.far_lower_cross_model_candidate)}` : ' · 未形成一致日期'}</small>`
          : '';
        const maxChannelMismatchInfo = item.date_max_channel_mismatch_candidate
          ? `<small>最大通道跨模型不匹配日期：${escapeHtml(item.date_max_channel_mismatch_candidate)} · ${escapeHtml(item.date_max_channel_mismatch_note || '')}</small>`
          : '';
        const maxChannelConsensusInfo = item.date_max_channel_consensus_candidate
          ? `<small>最大通道 3 单元格日期共识：${escapeHtml(item.date_max_channel_consensus_candidate)} · ${escapeHtml(item.date_max_channel_consensus_note || '')}</small>`
          : '';
        const partialYearDayBeforeInfo = item.date_partial_year_day_before_candidate
          ? `<small class="low-note">残缺年份四单元人工建议：${escapeHtml(item.date_partial_year_day_before_candidate)} · ${escapeHtml(item.date_partial_year_day_before_note || '')}</small>`
          : '';
        const dominantDateInfo = item.date_server_mobile_dominance_candidate
          ? `<small>Server/Mobile 跨区域主证据：${escapeHtml(item.date_server_mobile_dominance_candidate)} · ${escapeHtml(item.date_server_mobile_dominance_note || '')}</small>`
          : '';
        const repeatedServerDateInfo = item.date_repeated_server_candidate
          ? `<small>Server 三预处理完整日期：${escapeHtml(item.date_repeated_server_candidate)} · ${escapeHtml(item.date_repeated_server_note || '')}</small>`
          : '';
        const serverMobileComponentInfo = item.date_server_mobile_component_candidate
          ? `<small>Server 双几何 + Mobile 残缺组件共识：${escapeHtml(item.date_server_mobile_component_candidate)} · ${escapeHtml(item.date_server_mobile_component_note || '')}</small>`
          : '';
        const crossYearConsensusInfo = item.date_cross_year_consensus_candidate
          ? `<small>跨年完整日期双模型/双几何共识：${escapeHtml(item.date_cross_year_consensus_candidate)} · ${escapeHtml(item.date_cross_year_consensus_note || '')}</small>`
          : '';
        const componentConsensusInfo = item.date_component_consensus_candidate
          ? `<small>年月日分槽双模型共识：${escapeHtml(item.date_component_consensus_candidate)} · ${escapeHtml(item.date_component_consensus_note || '')}</small>`
          : '';
        const serverComponentConsensusInfo = item.date_server_component_candidate
          ? `<small>Server 双几何完整日期 + 固定槽位共识：${escapeHtml(item.date_server_component_candidate)} · ${escapeHtml(item.date_server_component_note || '')}</small>`
          : '';
        const daySlotConsensusInfo = item.date_day_slot_consensus_candidate
          ? `<small>两位日窄槽共识：${escapeHtml(item.date_day_slot_consensus_candidate)} · ${escapeHtml(item.date_day_slot_consensus_note || '')}</small>`
          : '';
        const missingYearSeparatorInfo = item.date_missing_year_separator_candidate
          ? `<small>漏“年”完整日期 + 去单位日槽共识：${escapeHtml(item.date_missing_year_separator_candidate)} · ${escapeHtml(item.date_missing_year_separator_note || '')}</small>`
          : '';
        const nondestructiveCrossYearInfo = item.date_cross_year_nondestructive_candidate
          ? `<small>跨年非破坏性原图共识：${escapeHtml(item.date_cross_year_nondestructive_candidate)} · ${escapeHtml(item.date_cross_year_nondestructive_note || '')}</small>`
          : '';
        const monthSlotConflictInfo = item.date_month_slot_conflict_candidate
          ? `<small>月份窄槽冲突解析：${escapeHtml(item.date_month_slot_conflict_candidate)} · 排除 ${escapeHtml(item.date_month_slot_discarded_conflict || '')} · ${escapeHtml(item.date_month_slot_conflict_note || '')}</small>`
          : '';
        const businessYearMonthDayInfo = item.date_unanimous_month_day_business_year_candidate
          ? `<small>跨模型月日一致 + 三业务日期年份共识：${escapeHtml(item.date_unanimous_month_day_business_year_candidate)} · 排除旧年份候选 ${escapeHtml(item.date_unanimous_month_day_business_year_discarded || '')} · ${escapeHtml(item.date_unanimous_month_day_business_year_note || '')}</small>`
          : '';
        const missingMonthBusinessInfo = item.date_partial_year_missing_month_candidate
          ? `<small>残缺年份 + 缺月份跨槽位共识：${escapeHtml(item.date_partial_year_missing_month_candidate)} · ${escapeHtml(item.date_partial_year_missing_month_note || '')}</small>`
          : '';
        const upperDateCards = item.upper_date_line_original_url
          ? `${artifactCard('盖章行手写日期', item.upper_date_line_original_url)}${artifactCard('盖章行日期去印章色后', item.upper_date_line_color_clean_url)}`
          : '';
        const farLowerPaddedCard = item.far_lower_padded_line_url
          ? artifactCard('远下方日期行右侧补白图（Mobile/Server）', item.far_lower_padded_line_url)
          : '';
        const slotCards = item.date_slot_year_original_url
          ? `${artifactCard('年份槽位原图', item.date_slot_year_original_url)}${artifactCard('年份槽位最大通道增强', item.date_slot_year_processed_url)}${item.date_slot_year_line_clean_url ? artifactCard('年份槽位去横线增强', item.date_slot_year_line_clean_url) : ''}${item.date_slot_year_white_url ? artifactCard('年份槽位白底标准化（Paddle 双模型）', item.date_slot_year_white_url) : ''}${item.date_slot_month_context_original_url ? artifactCard('月份上下文槽位原图', item.date_slot_month_context_original_url) : ''}${item.date_slot_month_context_processed_url ? artifactCard('月份上下文槽位最大通道增强', item.date_slot_month_context_processed_url) : ''}${item.date_slot_day_context_original_url ? artifactCard('日期上下文槽位原图', item.date_slot_day_context_original_url) : ''}${item.date_slot_day_context_processed_url ? artifactCard('日期上下文槽位最大通道增强', item.date_slot_day_context_processed_url) : ''}${item.date_slot_adaptive_day_original_url ? artifactCard('左移两位日自适应原图', item.date_slot_adaptive_day_original_url) : ''}${item.date_slot_adaptive_day_processed_url ? artifactCard('左移两位日去印章色后', item.date_slot_adaptive_day_processed_url) : ''}${item.date_slot_day_context_white_url ? artifactCard('日期上下文白边标准化（冲突复核）', item.date_slot_day_context_white_url) : ''}${item.date_slot_month_digit_original_url ? artifactCard('月份数字窄槽原图', item.date_slot_month_digit_original_url) : ''}${item.date_slot_month_digit_processed_url ? artifactCard('月份数字窄槽最大通道增强', item.date_slot_month_digit_processed_url) : ''}${item.date_slot_month_digit_line_clean_url ? artifactCard('月份数字窄槽去横线增强', item.date_slot_month_digit_line_clean_url) : ''}${artifactCard('月日联合槽位原图', item.date_slot_month_day_original_url)}${artifactCard('月日联合槽位最大通道增强', item.date_slot_month_day_processed_url)}${item.date_slot_month_day_line_clean_url ? artifactCard('月日槽位去横线增强', item.date_slot_month_day_line_clean_url) : ''}${item.date_slot_month_day_vision_url ? artifactCard('月日槽位裁后去章色白底', item.date_slot_month_day_vision_url) : ''}`
          : '';
        const daySlotCards = item.date_slot_day_digit_original_url
          ? `${artifactCard('日数字窄槽原图', item.date_slot_day_digit_original_url)}${artifactCard('日数字窄槽最大通道增强', item.date_slot_day_digit_processed_url)}${item.date_slot_day_digit_line_clean_url ? artifactCard('日数字窄槽去横线增强', item.date_slot_day_digit_line_clean_url) : ''}`
          : '';
        const innerDaySlotCards = item.date_slot_day_inner_original_url
          ? `${artifactCard('去单位日数字窄槽原图', item.date_slot_day_inner_original_url)}${artifactCard('去单位日数字窄槽最大通道增强', item.date_slot_day_inner_processed_url)}${item.date_slot_day_inner_line_clean_url ? artifactCard('去单位日数字窄槽去横线增强', item.date_slot_day_inner_line_clean_url) : ''}`
          : '';
        const slotTexts = (item.date_slot_ocr_variants || []).map(row => `${row.method}：${(row.ocr_texts || []).join(' | ') || '未识别'}`).join('；');
        const slotInfo = item.date_slot_year_original_url
          ? `<small>槽位 OCR：${escapeHtml(slotTexts)}${item.date_slot_candidate ? ` · ${item.date_slot_reliable ? '可靠候选' : '候选'} ${escapeHtml(item.date_slot_candidate)}` : ''}${item.date_slot_candidate_source ? ` · ${escapeHtml(item.date_slot_candidate_source)}` : ''}</small><small class="${item.date_slot_reliable ? '' : 'low-note'}">${escapeHtml(item.date_slot_acceptance_note || '')}</small>`
          : '';
        const daySlotTexts = (item.date_slot_day_ocr_variants || []).map(row => `${row.method}：${(row.ocr_texts || []).join(' | ') || '未识别'}`).join('；');
        const daySlotInfo = item.date_slot_day_digit_original_url
          ? `<small>日数字窄槽 OCR：${escapeHtml(daySlotTexts)}${item.date_slot_day_candidate ? ` · ${item.date_slot_day_reliable ? '可靠候选' : '候选'} ${escapeHtml(item.date_slot_day_candidate)}` : ''}</small><small class="${item.date_slot_day_reliable ? '' : 'low-note'}">${escapeHtml(item.date_slot_day_acceptance_note || '')}</small>`
          : '';
        const innerDaySlotTexts = (item.date_slot_day_inner_ocr_variants || []).map(row => `${row.method}：${(row.ocr_texts || []).join(' | ') || '未识别'}`).join('；');
        const innerDaySlotInfo = item.date_slot_day_inner_original_url
          ? `<small>去单位日数字窄槽 OCR：${escapeHtml(innerDaySlotTexts)}${item.date_slot_day_inner_candidate ? ` · ${item.date_slot_day_inner_reliable ? '可靠候选' : '候选'} ${escapeHtml(item.date_slot_day_inner_candidate)}` : ''}</small><small class="${item.date_slot_day_inner_reliable ? '' : 'low-note'}">${escapeHtml(item.date_slot_day_inner_acceptance_note || '')}</small>`
          : '';
        return `<article class="artifact-group"><h4>${escapeHtml(item.variant)}</h4><div class="artifact-grid">${artifactCard('仅手写日期行原图', item.date_line_original_url, lineText(['日期行原图']))}${artifactCard('日期行去红章', item.date_line_color_clean_url, lineText(['日期行去印章色']))}${item.date_line_positioned_frame_clean_url ? artifactCard('日期行去外框', item.date_line_positioned_frame_clean_url, lineText(['日期行去外框'])) : ''}</div></article>`;
      }).join('');
    } else {
      target.innerHTML = artifacts.map(item => {
        activeArtifactItem = item;
        const reference = item.visual_reference_match;
        const orientationInfo = item.orientation
          ? `<article class="artifact-group"><h4>${item.orientation.anchor_text ? '圆章文字方向' : '矩形印章方向'} <small>${escapeHtml(item.orientation.status || '')}${item.orientation.angle != null ? ` · 检测角度 ${Number(item.orientation.angle).toFixed(1)}°` : ''}${item.orientation.confidence ? ` · ${percent(item.orientation.confidence)}` : ''}</small></h4><div class="artifact-grid">${artifactCard('方向校正前原图', item.orientation_original_url || item.original_url)}${item.color_isolated_oriented_url ? artifactCard('按章型文字旋正后的保留章色图', item.color_isolated_oriented_url) : ''}${item.orientation.applied_rotation === 180 ? artifactCard('自动旋转 180° 后（后续 OCR 输入区域）', item.orientation_corrected_url) : ''}</div></article>` : '';
        const consensus = reference?.consensus_matches || [];
        const consensusInfo = ['multi_reference_consensus', 'high_purity_multi_reference_consensus', 'chromatic_crop_multi_reference_consensus', 'color_mask_multi_reference_consensus', 'strong_prefix_color_mask_multi_reference_consensus'].includes(reference?.route)
          ? ` · 多参考一致 ${reference.consensus_reference_count || consensus.length} 份（${consensus.map(row => `${escapeHtml(row.reference_filename || '')} ${['color_mask_multi_reference_consensus', 'strong_prefix_color_mask_multi_reference_consensus'].includes(reference?.route) ? `墨迹 ${percent(row.color_mask_score || 0)}` : `${row.homography_inliers || 0}内点`}`).join('、')}）`
          : '';
        const regularGeometry = reference?.regular_geometry;
        const regularGeometryInfo = regularGeometry
          ? ` · 排除黑色噪声前 ${regularGeometry.homography_inliers || 0}/${regularGeometry.good_matches || 0}内点，覆盖 ${percent(Math.min(regularGeometry.candidate_coverage || 0, regularGeometry.reference_coverage || 0))}`
          : '';
        const chromaticGeometryInfo = ['company_conflict_ultra_reference', 'receiving_one_glyph_reference', 'bare_company_one_glyph_reference', 'clipped_prefix_company_ultra_reference'].includes(reference?.route)
          ? ` · 章色裁剪内点 ${reference.chromatic_homography_inliers || 0}/${reference.chromatic_good_matches || 0} · 内点率 ${percent(reference.chromatic_inlier_ratio || 0)} · 覆盖 ${percent(Math.min(reference.chromatic_candidate_coverage || 0, reference.chromatic_reference_coverage || 0))}`
          : '';
        const acceptedRouteLabel = {
          multi_reference_consensus: '多参考一致通过',
          high_purity_multi_reference_consensus: '多参考高纯度一致通过',
          chromatic_crop_multi_reference_consensus: '章色稳健裁剪多参考一致通过',
          color_mask_multi_reference_consensus: '整体彩色墨迹多参考一致通过',
          strong_prefix_color_mask_multi_reference_consensus: '强文字前缀与章色多参考一致通过',
          trimmed_chromatic_single_reference: '稀疏章色噪点裁剪高纯度通过',
          color_mask_geometry: '彩色墨迹整体一致通过',
          color_mask_sift_geometry: '彩色墨迹与 SIFT 联合一致通过',
          high_ratio_single_reference: '高内点率路线通过',
          high_support_minor_coverage: '高支持度微覆盖路线通过',
          ultra_support_partial_coverage: '超高支持度局部覆盖路线通过',
          branded_station_single_reference: '三星服务中心站号章结构与几何联合通过',
          company_conflict_ultra_reference: '公司冲突下超强同章参考联合通过',
          receiving_one_glyph_reference: '收货章单字冲突双表示参考通过',
          bare_company_one_glyph_reference: '纯公司章单字冲突三表示参考通过',
          clipped_prefix_company_ultra_reference: '公司前缀截断双表示参考联合通过',
        }[reference?.route] || '达到单参考严格门槛';
        const referenceInfo = reference
          ? `<small class="${reference.accepted ? '' : 'low-note'}">参考章几何复核：${reference.accepted ? acceptedRouteLabel : '未达到门槛'} · 内点 ${reference.homography_inliers || 0}/${reference.good_matches || 0} · 内点率 ${percent(reference.inlier_ratio || 0)} · 章面覆盖 ${percent(Math.min(reference.candidate_coverage || 0, reference.reference_coverage || 0))}${regularGeometryInfo}${chromaticGeometryInfo}${reference.color_mask_score ? ` · 彩色墨迹 ${percent(reference.color_mask_score)}（相关 ${percent(reference.color_mask_correlation || 0)} / Dice ${percent(reference.color_mask_dice || 0)}）` : ''} · 参考 ${escapeHtml(reference.reference_filename || '')}${consensusInfo}</small>`
          : '';
        const referenceCard = reference?.reference_url
          ? artifactCard(`人工真值阳性参考章 · ${reference.reference_filename}`, reference.reference_url)
          : '';
        const originalText = item.isolated_text || item.secondary_original_text || '';
        const isolatedText = item.isolated_text || '';
        const colorText = [item.color_isolated_text, item.secondary_color_isolated_text].filter(Boolean).join(' | ');
        const bandText = item.round_type_band_text || '';
        const codeText = [item.code_line_text, item.secondary_code_line_text].filter(Boolean).join(' | ');
        const unwrapText = [item.unwrapped_text, item.secondary_unwrapped_text, item.server_audit_text].filter(Boolean).join(' | ');
        const rotatedText = [item.rotated_text, item.secondary_rotated_text].filter(Boolean).join(' | ');
        if (item.seal_model_backend) {
          return `${orientationInfo}<article class="artifact-group"><h4>收货印章 ${item.index + 1} · Paddle 印章专用</h4><div class="artifact-grid">${(item.seal_model_readings || []).map(reading => `<div>${artifactCard(reading.label, reading.url, reading.error ? `识别失败：${reading.error}` : (reading.texts || []).join(' | '))}<small>${reading.used_for_matching ? '独立读取后参与比对' : '原图仅供对照，不参与匹配'}${reading.observations?.length ? ` · 置信度 ${reading.observations.map(row => percent(row.confidence)).join(' / ')}` : ''}</small></div>`).join('')}</div></article>`;
        }
        return `${orientationInfo}<article class="artifact-group"><h4>收货印章 ${item.index + 1} · ${escapeHtml(item.color)} · ${escapeHtml(item.shape || '未知形状')} ${item.note ? `<small>${escapeHtml(item.note)}</small>` : `<small>增强 OCR：${escapeHtml(item.unwrapped_text || '未识别')}</small>`}${item.orientation_anchor_text ? `<small>章型方向锚点：${escapeHtml(item.orientation_anchor_text)}${item.color_isolated_oriented_text ? ` · 旋正 OCR：${escapeHtml(item.color_isolated_oriented_text)}` : ''}</small>` : ''}${item.color_isolated_text || item.secondary_color_isolated_text ? `<small>保留章色 OCR：${escapeHtml(item.color_isolated_text || item.secondary_color_isolated_text)}</small>` : ''}${item.code_line_text || item.secondary_code_line_text ? `<small>编号行 OCR：${escapeHtml(item.code_line_text || item.secondary_code_line_text)}</small>` : ''}${item.rotated_text || item.secondary_rotated_text ? `<small>180° 旋转 OCR：${escapeHtml(item.rotated_text || item.secondary_rotated_text)}</small>` : ''}${item.same_region_reconstructed_text ? `<small>同章区完整片段重组：${escapeHtml(item.same_region_reconstructed_text)}</small>` : ''}${item.overlapping_region_reconstructed_text ? `<small>重叠章区精确片段重组：${escapeHtml(item.overlapping_region_reconstructed_text)}</small><small>${escapeHtml(item.overlapping_region_acceptance_note || '')}</small>` : ''}${item.partitioned_service_reconstructed_text ? `<small>圆章跨分带精确重组：${escapeHtml(item.partitioned_service_reconstructed_text)}</small><small>${escapeHtml(item.partitioned_service_acceptance_note || '')}</small>` : ''}${item.conflict_mobile_band_text ? `<small>Mobile 矩形分带：${escapeHtml(item.conflict_mobile_band_text)}</small><small class="${item.conflict_mobile_band_resolution ? '' : 'low-note'}">${escapeHtml(item.conflict_mobile_band_acceptance_note || '')}</small>` : ''}${item.round_type_band_text ? `<small>${item.color_isolated_oriented_url ? '旋正后圆章章型横向分带' : '圆章章型分带'}：${escapeHtml(item.round_type_band_text)} · 置信度 ${escapeHtml((item.round_type_band_confidences || []).map(value => `${Math.round(Number(value) * 100)}%`).join(' / ') || '未知')}</small><small class="${item.round_type_band_reconstructed_text ? '' : 'low-note'}">${escapeHtml(item.round_type_band_acceptance_note || '')}</small>` : ''}${item.round_type_band_reconstructed_text ? `<small>章型单字纠错结果：${escapeHtml(item.round_type_band_reconstructed_text)}</small>` : ''}${item.robust_mobile_text || item.robust_server_text ? `<small>稳健圆心 Mobile：${escapeHtml(item.robust_mobile_text || '未识别')}</small><small>稳健圆心 Server：${escapeHtml(item.robust_server_text || '未识别')}</small><small class="${item.robust_shared_suffix ? '' : 'low-note'}">${escapeHtml(item.robust_bounds_acceptance_note || '')}</small>` : ''}${item.server_audit_text ? `<small>Server 大模型复核：${escapeHtml(item.server_audit_text)}</small>` : ''}${item.server_audit_rejection_reason ? `<small class="low-note">${escapeHtml(item.server_audit_rejection_reason)}</small>` : ''}${referenceInfo}${item.combined_text ? `<small>同一区域融合证据：${escapeHtml(item.combined_text)}</small>` : ''}</h4><div class="artifact-grid">${artifactCard('印章原始区域', item.original_url)}${artifactCard('颜色分离高对比图', item.isolated_url)}${item.color_isolated_url ? artifactCard('保留章色白底图', item.color_isolated_url) : ''}${item.color_isolated_oriented_url ? artifactCard('按章型文字旋正后的保留章色图', item.color_isolated_oriented_url) : ''}${item.round_type_band_url ? artifactCard(item.color_isolated_oriented_url ? '旋正后圆章章型横向分带' : '圆章章类型横向分带', item.round_type_band_url) : ''}${reference?.candidate_chromatic_crop_url ? artifactCard('章色稳健裁剪图（SIFT）', reference.candidate_chromatic_crop_url) : ''}${reference?.candidate_trimmed_chromatic_crop_url ? artifactCard('去稀疏噪点章色裁剪图（SIFT）', reference.candidate_trimmed_chromatic_crop_url) : ''}${item.code_line_url ? artifactCard('矩形编号章数字行', item.code_line_url) : ''}${artifactCard(item.shape === '矩形' ? '矩形印章校正图' : '圆章环形文字展开图', item.unwrapped_url)}${(item.unwrapped_band_urls || []).map((url, index) => artifactCard(`圆章展开独立分带 ${index + 1}`, url)).join('')}${item.robust_unwrapped_url ? artifactCard('稳健边界圆章展开图', item.robust_unwrapped_url) : ''}${(item.robust_unwrapped_band_urls || []).map((url, index) => artifactCard(`稳健圆心独立分带 ${index + 1}`, url)).join('')}${(item.conflict_mobile_band_urls || []).map((url, index) => artifactCard(`矩形章横向分带 ${index + 1}`, url)).join('')}${item.unwrapped_rotated_url ? artifactCard('圆章展开 180° 图', item.unwrapped_rotated_url) : ''}${item.color_isolated_rotations_url ? artifactCard('保留章色旋转对照图', item.color_isolated_rotations_url) : ''}${item.rotated_url ? artifactCard('矩形印章 180° 旋转图', item.rotated_url) : ''}${referenceCard}</div></article>`;
      }).join('');
    }
  }

  function artifactCard(label, url, recognizedText = '') {
    if (typeof url !== 'string' || !url.startsWith('/files/')) return '';
    const item = activeArtifactItem || {};
    const variantsText = (variants, labels) => (variants || [])
      .filter(row => !labels || labels.includes(row.preprocessing))
      .flatMap(row => row.ocr_texts || [])
      .filter(Boolean)
      .join(' | ');
    const inferredText = recognizedText || (
      label === '原始手写日期区域' ? variantsText(item.ocr_variants, ['原始裁剪']) :
      label === '去除彩色印章后' ? variantsText(item.ocr_variants, ['去印章色']) :
      label === '去表格线增强后' ? variantsText(item.ocr_variants, ['去表格线']) :
      label === '仅手写日期行' ? variantsText(item.date_line_ocr_variants, ['日期行原图']) :
      label === '日期行去印章色后' ? variantsText(item.date_line_ocr_variants, ['日期行去印章色']) :
      label === '日期行去表格线后' ? variantsText(item.date_line_ocr_variants, ['日期行去表格线']) :
      label === '日期行去外框' ? variantsText(item.date_line_display_ocr_variants, ['日期行去外框']) :
      label === '日期行去表格线三倍放大' ? variantsText(item.date_line_display_ocr_variants, ['日期行去表格线三倍放大']) :
      label === '日期行灰度自动对比三倍放大' ? variantsText(item.date_line_display_ocr_variants, ['日期行灰度自动对比三倍放大']) :
      label === '日期行最大通道去彩色三倍放大' ? variantsText(item.date_line_display_ocr_variants, ['日期行最大通道去彩色三倍放大']) :
      label === '日期行 Otsu 二值三倍放大' ? variantsText(item.date_line_display_ocr_variants, ['日期行 Otsu 二值三倍放大']) :
      label === '原日期行白底标准化' ? variantsText(item.date_line_display_ocr_variants, ['原日期行白底标准化']) :
      label === '印章原始区域' ? (item.isolated_text || item.secondary_original_text || '') :
      label === '颜色分离高对比图' ? (item.isolated_text || '') :
      label === '保留章色白底图' ? [item.color_isolated_text, item.secondary_color_isolated_text].filter(Boolean).join(' | ') :
      label === '圆章章类型横向分带' ? (item.round_type_band_text || '') :
      label === '矩形编号章数字行' ? [item.code_line_text, item.secondary_code_line_text].filter(Boolean).join(' | ') :
      label === '圆章环形文字展开图' || label === '矩形印章校正图' ? [item.unwrapped_text, item.secondary_unwrapped_text, item.server_audit_text].filter(Boolean).join(' | ') :
      label === '圆章展开 180° 图' || label === '矩形印章 180° 旋转图' ? [item.rotated_text, item.secondary_rotated_text].filter(Boolean).join(' | ') :
      ''
    );
    const text = String(inferredText || '').trim();
    const ocr = text ? `<p class="artifact-ocr-text">识别：${escapeHtml(text)}</p>` : '';
    return `<figure><a href="${escapeHtml(url)}" target="_blank" rel="noopener"><img src="${escapeHtml(url)}" alt="${escapeHtml(label)}" loading="lazy"></a><figcaption>${escapeHtml(label)}</figcaption>${ocr}</figure>`;
  }

  async function renderHistory() {
    const id = reviewState.current.id, content = $('#review-content').firstElementChild;
    const history = await api(`/api/results/${id}/review-history`);
    if (reviewState.current?.id !== id || $('#review-content').firstElementChild !== content) return;
    $('[data-history]', $('#review-content')).innerHTML = history.length ? history.map(row => `<div class="history-item"><b>${escapeHtml(row.action)}</b><span>${escapeHtml(row.changed_at)}</span><p>${escapeHtml(historySummary(row))}</p></div>`).join('') : '<div class="empty-state">尚无人工修改记录</div>';
  }

  async function renderGroundTruth() {
    const id = reviewState.current.id, content = $('#review-content').firstElementChild;
    const truth = await api(`/api/results/${id}/ground-truth`);
    if (reviewState.current?.id !== id || $('#review-content').firstElementChild !== content) return;
    const root = $('#review-content');
    $('[data-truth-status]', root).textContent = truth.exists ? '该样本已有真值，可审计更新' : '尚未标注，可在确认时新增';
    if (!reviewState.reviewDirty) $('[name=truth_seal_should_match]', root).value = truth.exists
      ? String(Boolean(truth.entry?.seal_should_match)) : '';
    const history = truth.history || [];
    $('[data-truth-history]', root).innerHTML = history.length
      ? `<small>最近真值记录：${escapeHtml(history[0].action)} · ${escapeHtml(history[0].changed_at)}</small>`
      : '<small>尚无真值修改历史</small>';
  }

  function historySummary(row) {
    try {
      const before = JSON.parse(row.before_json || '{}'), after = JSON.parse(row.after_json || '{}'), changed = [];
      const oldFields = before.fields || {}, newFields = after.fields || {};
      fieldSchema.output.filter(key => key !== '签收日期').forEach(key => { if ((oldFields[key] || '') !== (newFields[key] || '')) changed.push(`${key}: ${oldFields[key] || '空'} → ${newFields[key] || '空'}`); });
      const oldDate = before.date_check?.actual || '', newDate = after.date_check?.actual || '';
      if (oldDate !== newDate) changed.push(`实际日期: ${oldDate || '空'} → ${newDate || '空'}`);
      const oldSeal = before.seal_check?.recognized || '', newSeal = after.seal_check?.recognized || '';
      if (oldSeal !== newSeal) changed.push(`印章: ${oldSeal || '空'} → ${newSeal || '空'}`);
      const oldProductRows = before.product_table?.rows || [], newProductRows = after.product_table?.rows || [];
      newProductRows.forEach((row, index) => Object.keys(row.values || {}).forEach(column => {
        const oldValue = oldProductRows[index]?.values?.[column] || '', newValue = row.values?.[column] || '';
        if (oldValue !== newValue) changed.push(`商品第 ${index + 1} 行 ${column}: ${oldValue || '空'} → ${newValue || '空'}`);
      }));
      return [row.note, row.error_type, ...changed].filter(Boolean).join('；') || '状态已更新';
    } catch (_) { return row.note || row.error_type || '状态已更新'; }
  }

  return { fieldEditor, renderProductTable, renderArtifacts, artifactCard, renderHistory, renderGroundTruth, historySummary};
}
