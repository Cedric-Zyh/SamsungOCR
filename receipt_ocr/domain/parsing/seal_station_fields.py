"""Compare service-station seals by independently observed identity fields."""
import re


_IDENTITY = re.compile(
    r"(?P<center>[\u4e00-\u9fff]{2,20}?服务中心)"
    r"(?:(?P<name>[\u4e00-\u9fff]{3,12}):?站代码:?"
    r"|站代码:?(?P<reordered_name>[\u4e00-\u9fff]{3,12}):?)"
    r"(?P<code>[0-9]{6,12})"
)
_PHONE_LABEL = re.compile(r"TEL:?|电话:?", re.IGNORECASE)
_CONTACT = re.compile(
    r"(?P<phone>0[0-9]{2,3}-[0-9]{7,8})"
    r"(?P<address>(?:地址:)?[\u4e00-\u9fff]{2,12}市"
    r"[^\n]{1,100}(?:路|街|巷|大道)[0-9]{1,6}号[^\n]{0,80})?"
)
_IDENTITY_IN_CONTACT = re.compile(r"公司|分部|中心|授权|代码|编号|站点|专用章|收货章|业务受理|TEL|电话", re.IGNORECASE)


def compare_station_fields(expected, actual):
    """Ignore only delimited contact fields; never join separate stamp reads.

    The requirement must contain a centre, station name and explicit station
    code only. A one-character suffix completing a printed station-name prefix
    is allowed with a visible notice, only when centre and full code agree.
    Different names, missing code digits and extra required fields stay strict.
    """
    required = _IDENTITY.fullmatch(expected)
    if not required:
        return None
    labels = list(_PHONE_LABEL.finditer(actual))
    additional = []
    identity = actual
    if labels:
        if len(labels) != 1:
            return None
        label = labels[0]
        contact = _CONTACT.fullmatch(actual[label.end():])
        if not contact or _IDENTITY_IN_CONTACT.search(contact['address'] or ''):
            return None
        identity = actual[:label.start()]
        additional.append('电话')
        if contact['address']:
            additional.append('地址')
    observed = _IDENTITY.fullmatch(identity)
    if not observed or any(required[key] != observed[key] for key in ('center', 'code')):
        return None
    name = required['name'] or required['reordered_name']
    actual_name = observed['name'] or observed['reordered_name']
    differences = []
    if name != actual_name:
        if not actual_name.startswith(name) or len(actual_name) != len(name) + 1:
            return None
        differences.append({'field': '站点名称', 'required': name, 'recognized': actual_name})
    message = f"服务中心名称、站代码{required['code']}一致"
    if differences:
        message += f"；站点名称差异：要求“{name}”，印章“{actual_name}”（要求名称为印章名称前缀）"
    if additional:
        message += '；' + '、'.join(additional) + '为签章要求未列出的附加信息'
    return {
        'matched_fields': ['服务中心名称', '站代码'] + ([] if differences else ['站点名称']),
        'additional_fields': additional,
        'name_differences': differences,
        'required_fields': {'服务中心名称': required['center'], '站代码': required['code'], '站点名称': name},
        'recognized_fields': {'服务中心名称': observed['center'], '站代码': observed['code'], '站点名称': actual_name},
        'message': message,
    }
