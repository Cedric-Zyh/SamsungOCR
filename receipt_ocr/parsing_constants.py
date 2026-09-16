"""Shared parsing thresholds, template labels and reviewed business catalogs."""

from __future__ import annotations

import re


LOW_CONFIDENCE_THRESHOLD = 0.72

PRODUCT_COLUMNS = (
    "行号", "产品类别", "物料编号", "等级", "出库仓库", "数量", "重量", "体积", "EAN码",
)

# 固定版式的列边界。使用 OCR 文字框中心点归列，比依赖 OCR 自动插入空格稳定得多。
PRODUCT_COLUMN_RANGES = {
    "行号": (0.00, 0.115),
    "产品类别": (0.115, 0.22),
    "物料编号": (0.22, 0.43),
    "等级": (0.43, 0.49),
    "出库仓库": (0.49, 0.59),
    "数量": (0.59, 0.675),
    "重量": (0.675, 0.755),
    "体积": (0.755, 0.82),
    "EAN码": (0.82, 1.00),
}

PRODUCT_REQUIRED_COLUMNS = {
    "行号", "产品类别", "物料编号", "等级", "出库仓库", "数量", "重量", "体积", "EAN码",
}

# EAN is a check-digit-protected product identifier.  These recurring items
# were confirmed in multiple reviewed receipts; use the catalog only when the
# OCR material is already highly similar, preserving unknown/new products.
VERIFIED_MATERIAL_BY_EAN = {
    "8806094705492": "SM-S9180ZKHCHC悠远黑512G",
    "8806097031062": "F-RS938CBEGCN护盾减震型保护壳",
    "8806095906553": "F-VS938PBEGCN环保生态皮保护壳",
    "8806095307947": "SM-S9280ZKHCHC钛黑512G",
    "8806095787787": "SM-W9025ZDGCHC陶瓷黑1TB",
    "8806095635699": "F-VF741PYEGCN环保生态皮保护壳",
    "8806095299341": "SM-S9260ZKGCHC水墨黑512G",
    "8806095299389": "SM-S9260ZKDCHC水墨黑256G",
    # Independently printed on reviewed receipts 7303351778/7303624019.
    "8806097433736": "SM-F9660ZKGCHC秘影黑512G",
    # Independently printed on reviewed receipt 7320775545. OCR may drop the
    # final colour glyph because ``黑`` touches the following 512G text.
    "8806097727347": "SM-W9026AKDCHC玄曜黑512G",
}
VERIFIED_EAN_BY_MATERIAL = {
    material: ean for ean, material in VERIFIED_MATERIAL_BY_EAN.items()
}


FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "承运商": ("承运商",),
    "运单号": ("运单号",),
    "制单日期": ("制单日期",),
    "供应商": ("供应商", "共应商"),
    "客户名称": ("客户名称",),
    "客户地址": ("客户地址",),
    # PaddleOCR occasionally confuses the printed ``仓`` with ``片`` on the
    # standard template.  Keep this as an explicit observed label alias; it is
    # used only to locate the row and never changes the extracted value.
    "客户仓库": ("客户仓库", "客片仓库", "客仓库"),
    "客户订单号": ("客户订单号",),
    "销售订单号": ("销售订单号", "消售订单号"),
    "要求到货": ("要求到货",),
    # Reviewed PP-OCRv5 sample 7304159004 reads the fixed printed label as
    # ``收le址`` while recognizing the full address value at 99%.  This alias
    # is used only as a geometric label anchor; it never supplies field data.
    "收货地址": ("收货地址", "收le址"),
    "发货单位": ("发货单位",),
    "发货地址": ("发货地址",),
    "签章要求": ("签章要求", "签幸要求", "签草要求"),
    "签收说明": ("签收说明",),
    "客户电话": ("客户电话",),
    "仓库电话": ("仓库电话",),
    "手工订单号": ("手工订单号",),
}

# 当前三星回单模板中的固定说明。只有 OCR 文本与标准短语足够相似时才校正，
# 避免在模板发生变化或整行漏识别时凭空补值。
FIXED_FIELD_PHRASES: dict[str, tuple[str, ...]] = {
    "签收说明": ("如未签实收数量视为整单完整签收",),
    # Repeated template value learned from reviewed samples. Signature
    # requirements use the stricter field-specific threshold below so a
    # different company cannot be coerced by generic legal suffixes.
    "签章要求": ("京小服科技服务有限公司维修中心专用章（04）",),
}
FIXED_PHRASE_MIN_SIMILARITY = 0.78
FIXED_PHRASE_MIN_SIMILARITY_BY_FIELD = {"签章要求": 0.92}

# Reviewed customer-specific stamp policy.  This is business master data, not
# fuzzy OCR invention: it is applied only when the customer is independently
# repeated in the warehouse field and the noisy requirement still shares an
# organization prefix or an explicit stamp-type suffix.
VERIFIED_REQUIREMENT_BY_CUSTOMER = {
    "贵州宏羿科技有限公司": "贵州宏羿科技有限公司",
    "深圳市星睿奇光电有限公司": "深圳市星睿奇光电有限公司仓储部收货章",
    "合肥佳元电子通讯产品技术服务有限公司第一分公司": "合肥佳元电子第一分公司手机售后专用章",
    "郑州广利达电子技术有限公司": "郑州广利达电子技术有限公司业务受理专用章",
    "靖江市中联通讯设备经营部": "靖江市中联通讯售后专用章",
}

VERIFIED_PICKUP_REQUIREMENT_BY_STATION = {
    "5785258": "三星电子服务中心取机专用章5785258站",
    "6237143": "三星电子服务中心取机专用章（2）6237143站电话：02081061101",
}

# Reviewed business master data for pickup stations.  A station id is printed
# independently inside the signature requirement, so it can safely repair a
# one-glyph customer-name dropout only when both repeated organization fields
# agree and already closely resemble the audited customer.
VERIFIED_CUSTOMER_BY_STATION = {
    "6237143": "广州市新六菱电子科技有限公司",
}

# ``ShipToCode`` is an exact printed business identifier and therefore safer
# than guessing a rare customer-name character from language context.  Entries
# are added only after human review; the parser still requires two repeated,
# near-identical organization readings before applying one.
VERIFIED_CUSTOMER_BY_SHIP_TO_CODE = {
    "0006049067": "佛山市顺德区宇骥通讯器材有限公司",
    "0002300118": "乌鲁木齐贵迪电子有限公司",
    "0008374451": "合肥佳元电子通讯产品技术服务有限公司第一分公司",
    "0006237106": "上海信威摄影器材有限公司",
    "0003367583": "杭州松峰电子科技有限公司",
    "0005970275": "德州市德城区利星电子产品销售店（个体工商户）",
}

# Exact, human-reviewed station requirements keyed by the independently
# printed ShipToCode. Replacement still requires the customer and warehouse
# repetitions to agree with the same audited master and the OCR requirement to
# contain the station code.
VERIFIED_REQUIREMENT_BY_SHIP_TO_CODE = {
    "0003197601": (
        "吉林省欧昇科技有限公司",
        "三星电子授权服务中心0431-88693789",
    ),
    "0002310637": (
        "北京东润丽达科技有限公司",
        "三星电子维修中心2310637",
    ),
    "0006237106": (
        "上海信威摄影器材有限公司",
        "三星电子服务中心上海信威站代码6237106",
    ),
}

DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:[-./年])\s*(?P<month>\d{1,2})\s*(?:[-./月])\s*(?P<day>\d{1,2})\s*日?"
)
