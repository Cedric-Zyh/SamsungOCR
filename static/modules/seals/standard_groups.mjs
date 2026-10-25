// Presentation groups only: every standard keeps its own ID and requirement.
// The reviewed requirement is authoritative; OCR text must not change ownership.
const COMPANY_END = /(?:有限责任公司|股份有限公司|有限公司|股份公司|总公司|公司)$/u;
const COMPANY_PREFIX = /^(.+?(?:有限责任公司|股份有限公司|有限公司|股份公司|总公司|公司))(.+)$/u;
const INSTITUTION_END = /(?:中心|医院|学校|大学|研究院|研究所|委员会|管理局|工作站|服务站|仓库|仓(?:[A-Za-z0-9-]+)?|[A-Z]{1,5}\d[A-Za-z0-9-]*)$/u;
const SEAL_SUFFIX = /(?:收货专用章|收货章|收讫专用章|收讫章|签收专用章|签收章|验收专用章|验收章|业务专用章|业务专章|业务章|业务受理|合同专用章|合同章|财务专用章|财务章|发票专用章|发票章|结算专用章|结算章|货物收讫章|出入库专用章|出库专用章|入库专用章|收发货专用章|仓库专用章|维修专用章|检测专用章|售后业务专用章|手机售后专用章|三星售后服务专用章|三星售后专用章|售后服务专用章|售后专用章|维修中心专用章|公章|印章)(?:[（(][\d０-９一二三四五六七八九十百甲乙丙丁A-Za-z号、,，/／-]+[）)]|[①-⑳㉑-㊿\d０-９]+(?:[/／、,，][①-⑳㉑-㊿\d０-９]+)*)?$/u;
const AMBIGUOUS_OWNER = /[、,，;；/／]|(?:公司|中心)(?:或者|或|以及|及|与|和)|(?:或者|或|以及|及|与|和)$/u;
const GENERIC_CENTER = /^三星(?:电子)?(?:授权)?(?:维修|服务|维修服务)中心$/u;

const requirementText = standard => String(standard?.requirement || '').trim();
const compact = value => value.replace(/\s+/gu, '');
const identity = value => compact(value).normalize('NFKC');

function ownerDescription(owner) {
  // A full legal name is retained verbatim. Similar prefixes do not imply a group.
  if (owner.length >= 4 && COMPANY_END.test(owner) && !owner.endsWith('分公司')) {
    return {company: owner, branch: ''};
  }
  const parts = owner.match(COMPANY_PREFIX);
  if (parts) {
    const company = parts[1];
    const branch = parts[2].replace(/^[（(](.*)[）)]$/u, '$1');
    // A precise legal owner is enough to retain a place/department even when
    // the requirement says “上海” or “福州仓库” rather than “上海分公司”.
    if (company.length >= 4 && branch && !/[章:]|：/u.test(branch)
        && !branch.replace(/分公司/gu, '').includes('公司')
        && !AMBIGUOUS_OWNER.test(branch)) return {company, branch};
  }
  if (owner.length >= 4 && INSTITUTION_END.test(owner)
      && !AMBIGUOUS_OWNER.test(owner)) return {company: owner, branch: ''};
  return null;
}

/** Describe a reviewed requirement without inferring a company from OCR text. */
export function describeStandard(standard) {
  const requirement = requirementText(standard);
  const text = compact(requirement);
  const match = text.match(SEAL_SUFFIX);
  const seal = match?.[0] || '';
  const owner = match ? text.slice(0, match.index) : text;
  const description = ownerDescription(owner);
  if (description && !AMBIGUOUS_OWNER.test(owner) && !GENERIC_CENTER.test(owner)) {
    return {
      key: `company:${identity(description.company)}`,
      ...description,
      seal,
      label: [description.branch, seal].filter(Boolean).join(' · ') || requirement,
    };
  }
  return {
    key: requirement ? `requirement:${identity(requirement)}` : `unassigned:${standard?.id ?? ''}`,
    company: '', branch: '', seal: '', label: requirement || '未填写签章要求',
  };
}

/** Keep the server's newest-first order, while collecting exact company groups. */
export function groupStandards(standards) {
  const groups = new Map();
  standards.forEach((standard, index) => {
    const description = describeStandard(standard);
    // Missing requirements cannot be grouped using identical OCR or blank labels.
    const key = requirementText(standard) ? description.key : `${description.key}:${index}`;
    if (!groups.has(key)) groups.set(key, {
      key,
      title: description.company || description.label,
      items: [],
    });
    groups.get(key).items.push({standard, description});
  });
  for (const group of groups.values()) {
    if (group.items.length === 1) {
      const item = group.items[0];
      item.description = {...item.description, label: requirementText(item.standard) || item.description.label};
    }
  }
  return [...groups.values()];
}
