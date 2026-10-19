"""Stamp orientation: settings."""




DEFAULT_SEAL_ORIENTATION_MODE = "polygon"

SEAL_ORIENTATION_MODES = (
    {
        "id": "none",
        "label": "不处理",
        "description": "保留印章原方向，直接进行本地印章 OCR",
    },
    {
        "id": "polygon",
        "label": "文本框角度估计",
        "description": "用“用章/专用章”检测框的四点坐标估计角度",
    },
    {
        "id": "doc_ori",
        "label": "文档方向分类（doc_ori）",
        "description": "使用 PP-LCNet_x1_0_doc_ori，仅在置信度达到 90% 时按直角粗校正；圆章仍可能误判",
    },
    {
        "id": "combined",
        "label": "四方向择优校正",
        "description": "在整章中检测横排文字，比较四个方向；未读到时补试斜向角度，按完整章型行微调",
    },
)

_SEAL_ORIENTATION_MODE_IDS = {item["id"] for item in SEAL_ORIENTATION_MODES}

MIN_CONFIDENCE = 0.70

ROUND_STAMP_ANCHOR_MIN_CONFIDENCE = 0.70

DOC_ORIENTATION_MIN_CONFIDENCE = 0.90

RECTANGULAR_TEXT_MIN_CONFIDENCE = 0.45

RECTANGULAR_MAX_SKEW = 15.0

FOUR_WAY_COARSE_ANGLES = (0, 90, 180, 270)

FOUR_WAY_BAND_LEFT_RATIO = 0.06

FOUR_WAY_BAND_TOP_RATIO = 0.38

FOUR_WAY_BAND_RIGHT_RATIO = 0.94

FOUR_WAY_BAND_BOTTOM_RATIO = 0.64

FOUR_WAY_ANCHOR_MIN_CONFIDENCE = 0.45

FOUR_WAY_MIN_FINE_ROTATION = 2.0

FOUR_WAY_MAX_FINE_ROTATION = 45.0

FOUR_WAY_FINAL_ROW_MIN_CONFIDENCE = 0.55

def resolve_seal_orientation_mode(value: str | None) -> str:
    selected = str(value or DEFAULT_SEAL_ORIENTATION_MODE).strip().lower()
    if selected not in _SEAL_ORIENTATION_MODE_IDS:
        raise ValueError(f"不支持的印章方向处理方式：{selected}")
    return selected

def seal_orientation_mode_label(value: str | None) -> str:
    selected = resolve_seal_orientation_mode(value)
    return next(item["label"] for item in SEAL_ORIENTATION_MODES if item["id"] == selected)
