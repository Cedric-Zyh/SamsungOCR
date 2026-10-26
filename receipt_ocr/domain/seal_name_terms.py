"""Describe registered seal wording without inventing an owner or identity."""
import re

from .parsing.seal_script import simplify_seal_text

PURPOSES = tuple(sorted({
    '收货专用章', '收货章', '收讫专用章', '收讫章', '签收专用章', '签收章',
    '验收专用章', '验收章', '业务专用章', '业务专章', '业务章', '业务受理专用章',
    '业务受理章', '业务受理', '合同专用章', '合同章', '财务专用章', '财务章',
    '发票专用章', '发票章', '结算专用章', '结算章', '货物收讫章', '出入库专用章',
    '出库专用章', '入库专用章', '收发货专用章', '仓库专用章', '维修专用章',
    '检测专用章', '售后业务专用章', '手机售后专用章', '三星售后服务专用章',
    '三星售后专用章', '售后服务专用章', '售后专用章', '维修中心专用章',
    '取机专用章', '维修业务专用章', '服务专用章', '公章', '印章',
}, key=len, reverse=True))
_PURPOSE = re.compile('(?:' + '|'.join(PURPOSES) + ')$')
_LEGAL = re.compile(r'^(.+?(?:有限责任公司|股份有限公司|有限公司|股份公司|集团公司|总公司|公司))(.*)$')
_NUMBER = re.compile(r'(\([0-9]{1,3}\)|[①-⑳㉑-㊿](?:[/／、,，][①-⑳㉑-㊿])*)$')
_GENERIC = re.compile(r'^三星(?:电子)?(?:授权)?(?:维修|服务|维修服务)中心$')
_AMBIGUOUS = re.compile(r'[、,，;；/／]|(?:公司|中心)(?:或者|或|以及|及|与|和)')


def normalized_name(text):
    return simplify_seal_text(re.sub(r'\s+', '', str(text or ''))).translate(
        str.maketrans('（）０１２３４５６７８９', '()0123456789'))


def _kind(name, warehouse=False):
    if warehouse or name.endswith(('仓库', '仓')): return '仓库'
    if '体验店' in name: return '授权体验店' if '授权体验店' in name else '体验店'
    if name.endswith(('商店', '门店', '经营部', '经营店', '专卖店')): return '商店／门店'
    if name.endswith(('维修部', '服务部', '服务总汇')): return '维修／服务机构'
    if name.endswith(('服务站', '工作站', '售后站')) or name == '三星售后': return '服务站'
    if name.endswith('中心'): return '服务／维修中心'
    if name.endswith('分公司'): return '分公司'
    if name.endswith('公司'): return '公司'
    return ''


def _single_name_terms(requirement):
    original = str(requirement or '')
    text = normalized_name(original)
    rest = text
    identities = []

    def identity(kind, label, value):
        identities.append(dict(kind=kind, label=label, value=value))

    # Only isolated terminal stamp numbers; country names and warehouse codes
    # do not become stamp numbers. Circled alternatives keep their order.
    number_match = _NUMBER.search(rest)
    number = number_match[0] if number_match else ''
    if number_match: rest = rest[:number_match.start()]
    code = re.search(r'(授权代码|站代码)[:：]?([A-Za-z0-9-]+)$', rest)
    if code:
        identity('authorization_code' if code[1] == '授权代码' else 'station_code', code[1], code[2])
        rest = rest[:code.start()]
    phone = re.search(r'(?:(?:电话|TEL)[:：]?)?(0[1-9]\d{1,2}-\d{7,8})$', rest, re.I)
    if phone:
        identity('phone', '电话', phone[1]); rest = rest[:phone.start()]
    station = re.search(r'(\d{4,12})站$', rest)
    if station:
        identity('station_code', '站点编号', station[1]); rest = rest[:station.start()]
    digits = re.search(r'\d{6,14}$', rest)
    if digits:
        value = digits[0]; prefix = rest[:digits.start()]
        if prefix.endswith(('维修中心', '服务中心')):
            identity('station_code', '站点编号', value)
        elif re.fullmatch(r'0[1-9]\d{9,10}', value):
            identity('phone', '电话', value)
        else:
            identity('registration_code', '登记编号', value)
        rest = prefix
    purpose_match = _PURPOSE.search(rest)
    purpose = purpose_match[0] if purpose_match else ''
    if purpose_match: rest = rest[:purpose_match.start()]
    warehouse = re.search(r'(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]+$', rest)
    if warehouse:
        is_warehouse = 'DC' in warehouse[0].upper() or rest[:warehouse.start()].endswith(('仓', '仓库'))
        identity('warehouse_code' if is_warehouse else 'registration_code',
                 '仓库代码' if is_warehouse else '登记代码', warehouse[0])
        rest = rest[:warehouse.start()]
        warehouse = warehouse if is_warehouse else None

    company = branch = unclassified = ''
    kind = ''
    if rest and not _AMBIGUOUS.search(rest):
        legal = _LEGAL.fullmatch(rest)
        if legal:
            company, tail = legal.groups()
            kind = _kind(company)
            tail = re.sub(r'^\((.*)\)$', r'\1', tail)
            if tail:
                explicit = re.match(r'([\u3400-\u9fff]{1,16}?(?:分公司|分部|仓库|仓储部|客户服务中心))', tail)
                if explicit:
                    branch, unclassified = explicit[1], tail[explicit.end():]
                elif (re.fullmatch(r'[\u3400-\u9fff]{2,18}', tail) and not re.search(r'[章:：]', tail)
                        and (tail.endswith(('分公司', '分部', '仓库', '部', '中心', '服务站'))
                             or purpose and len(tail) <= 6)):
                    branch = tail
                else:
                    unclassified = tail
        else:
            kind = _kind(rest, warehouse=bool(warehouse))
            if kind: company = rest
            else: unclassified = rest
    else:
        unclassified = rest
    status = 'parsed' if company and not unclassified else 'partial' if company or purpose or identities or number else 'unparsed'
    supported = bool(company)
    return dict(company=company, branch=branch, name=company+branch, entity_type=kind,
                purpose=purpose, number=number, identity_fields=identities,
                unclassified=unclassified, parse_status=status, supported=supported,
                correction_supported=supported and not unclassified and len(company+branch) >= 6,
                groupable=supported and not unclassified and not identities and not _GENERIC.fullmatch(company),
                original=original, normalized=normalized_name(original))


def seal_name_terms(requirement):
    """Recognize explicit full alternatives of the exact same registered owner.

    A slash inside parentheses, a number choice, a code or a contact field
    remains literal. Each alternative must carry its own full owner and
    purpose; fields are never borrowed from the neighbouring wording.
    """
    original = str(requirement or '')
    pieces, start, depth = [], 0, 0
    for index, character in enumerate(original):
        if character in '(（': depth += 1
        elif character in ')）': depth = max(0, depth - 1)
        elif character in '/／' and depth == 0:
            # Circled-number alternatives are one field, not full wordings.
            following = original[index+1:].lstrip()
            if following and re.match(r'[①-⑳㉑-㊿]', following):
                continue
            pieces.append(original[start:index].strip()); start = index + 1
    if pieces:
        pieces.append(original[start:].strip())
    if not 2 <= len(pieces) <= 8 or not all(pieces):
        return _single_name_terms(original)
    variants = [_single_name_terms(piece) for piece in pieces]
    if (any(item['parse_status'] != 'parsed' or not item['purpose'] for item in variants)
            or len({item['name'] for item in variants}) != 1):
        return _single_name_terms(original)
    first = variants[0]
    common_identities = (first['identity_fields'] if all(
        item['identity_fields'] == first['identity_fields'] for item in variants) else [])
    return dict(first, original=original, normalized=normalized_name(original),
                purpose='', number='', identity_fields=common_identities,
                alternatives=variants,
                groupable=all(item['groupable'] for item in variants),
                correction_supported=all(item['correction_supported'] for item in variants))
