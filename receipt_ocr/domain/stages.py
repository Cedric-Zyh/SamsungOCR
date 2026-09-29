"""Recognition stage identity and execution order.

Order is business behavior: date and seal consume printed fields. Keep a
single sequence shared by plan validation and both execution entry points.
"""

from enum import Enum


class Stage(str, Enum):
    FIELDS = "fields"
    PRODUCTS = "products"
    HANDWRITING = "handwriting"
    DATE = "date"
    SEAL = "seal"


STAGES = tuple(stage.value for stage in Stage)
STAGE_LABELS = {
    Stage.FIELDS.value: "印刷字段",
    Stage.PRODUCTS.value: "商品明细",
    Stage.HANDWRITING.value: "手写签名",
    Stage.DATE.value: "签收日期",
    Stage.SEAL.value: "客户印章",
}
