"""Declare every date image's role before any OCR call."""
from ..contracts import DateCropRegion, DateInputKind as Kind, DateInputRole as Role
from ..contracts import PreparedDate, PreparedDateInput


def prepare_manifest(region: DateCropRegion) -> PreparedDate:
    images = region.images
    # The compact date flow has one recognition contract.  Clean derivatives
    # are decision inputs; the original crop is display-only.  ``AUDIT`` is
    # reserved for future evidence that is deliberately outside recognition,
    # and is not used by the live date pipeline.
    role = Role.DECISION
    region_id = f"date-{region.crop_key}"
    variants = (
        ("original", images.raw, Kind.ORIGINAL, Role.DISPLAY, "原始裁剪", "region"),
        ("color", images.color_clean, Kind.COLOR_CLEAN, role, "去印章色", "region"),
        ("table", images.crop, Kind.TABLE_CLEAN, role, "去表格线", "region"),
        ("line-original", images.line_raw, Kind.ORIGINAL, Role.DISPLAY, "日期行原图", "line"),
        ("line-color", images.line_color_clean, Kind.COLOR_CLEAN, role, "日期行去印章色", "line"),
        ("line-frame", images.line_positioned_frame_clean, Kind.FRAME_CLEAN, role, "日期行去外框", "line"),
    )
    return PreparedDate(region_id, tuple(
        PreparedDateInput(f"{region_id}:{name}", region_id, path, kind, usage, label, space)
        for name, path, kind, usage, label, space in variants if path is not None
    ))


def ensure_manifest(region: DateCropRegion) -> PreparedDate:
    if region.prepared is None:
        region.prepared = prepare_manifest(region)
    return region.prepared
