"""Select complete dates with conservative cross-model and geometry checks."""

from __future__ import annotations
import re
from datetime import date
from .parser import parse_date, parse_receipt_date
from .date_fragments import (
    _is_date_audit_only_preprocessing,
    _mobile_component_supports_date,
    _parse_missing_year_separator_full_date,
)


def _server_cross_geometry_date_with_mobile_components_from_artifacts(
    artifacts: list[dict],
) -> date | None:
    """Resolve one narrow Mobile conflict using Server and raw components.

    Server must read one and only one strict date from the max-channel line in
    both tight and wide crops. Mobile must preserve that date's OCR-owned
    year/month/day components in both geometries, while its own max-channel
    strict results must be two different dates that share one month/day. This
    identifies an unstable Mobile digit interpretation; no printed required
    date is passed to or used by this selector.
    """
    primary_variants = {"紧凑区域", "宽区域"}
    records: list[dict] = []
    for artifact in artifacts:
        variant = str(artifact.get("variant", ""))
        if variant not in primary_variants:
            continue
        for key, default_backend in (
            ("ocr_variants", str(artifact.get("ocr_backend", ""))),
            (
                "secondary_ocr_variants",
                str(artifact.get("secondary_ocr_backend", "")),
            ),
            (
                "date_line_ocr_variants",
                str(artifact.get("date_line_ocr_backend", "")),
            ),
        ):
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                label = f"{default_backend} {preprocessing}".lower()
                if "server" in label or "大模型" in label:
                    engine = "server"
                elif "paddle" in label or "mobile" in label:
                    engine = "mobile"
                else:
                    continue
                for raw_text in evidence.get("ocr_texts") or []:
                    text = str(raw_text).strip()
                    if text:
                        records.append(
                            {
                                "text": text,
                                "date": parse_date(text),
                                "engine": engine,
                                "variant": variant,
                                "max_channel": "最大通道" in preprocessing,
                            }
                        )
    server_by_variant = {
        variant: {
            item["date"]
            for item in records
            if item["engine"] == "server"
            and item["variant"] == variant
            and item["max_channel"]
            and item["date"] is not None
        }
        for variant in primary_variants
    }
    if any(len(values) != 1 for values in server_by_variant.values()):
        return None
    candidates = set().union(*server_by_variant.values())
    if len(candidates) != 1:
        return None
    candidate = next(iter(candidates))
    mobile_conflicts_by_variant = {
        variant: {
            item["date"]
            for item in records
            if item["engine"] == "mobile"
            and item["variant"] == variant
            and item["max_channel"]
            and item["date"] is not None
            and item["date"] != candidate
        }
        for variant in primary_variants
    }
    if any(len(values) != 1 for values in mobile_conflicts_by_variant.values()):
        return None
    mobile_conflicts = set().union(*mobile_conflicts_by_variant.values())
    if (
        len(mobile_conflicts) != 2
        or len({(value.month, value.day) for value in mobile_conflicts}) != 1
        or (candidate.month, candidate.day)
        in {(value.month, value.day) for value in mobile_conflicts}
    ):
        return None
    for variant in primary_variants:
        if not any(
            item["engine"] == "mobile"
            and item["variant"] == variant
            and _mobile_component_supports_date(item["text"], candidate)
            for item in records
        ):
            return None
    all_strict_dates = {item["date"] for item in records if item["date"] is not None}
    if all_strict_dates != {candidate, *mobile_conflicts}:
        return None
    return candidate


def _server_mobile_dominant_date_from_artifacts(
    artifacts: list[dict], required_text: str
) -> date | None:
    """Confirm one near-required date despite one isolated OCR interference.

    A complete Server reading alone is unsafe because both Paddle models can
    drop one digit from a two-digit handwritten day.  Promotion therefore
    requires Mobile to repeat the same date in both tight and wide crops, at
    least four geometry/preprocessing cells, and the candidate to be within
    three days of the requested delivery date.  At most one non-strict,
    Mobile-only conflicting cell is tolerated.  No ground-truth value is used.
    """
    required = parse_date(required_text)
    if required is None:
        return None
    observations: list[dict] = []
    for artifact in artifacts:
        variant = str(artifact.get("variant", ""))
        if variant not in {"紧凑区域", "宽区域"}:
            continue
        for key, default_backend in (
            ("ocr_variants", str(artifact.get("ocr_backend", ""))),
            (
                "secondary_ocr_variants",
                str(artifact.get("secondary_ocr_backend", "")),
            ),
            (
                "date_line_ocr_variants",
                str(artifact.get("date_line_ocr_backend", "")),
            ),
        ):
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                backend = default_backend
                if "Server" in preprocessing or "大模型" in preprocessing:
                    engine = "server"
                elif "Mobile" in preprocessing:
                    engine = "mobile"
                elif "server" in backend.lower() or "大模型" in backend:
                    engine = "server"
                elif "paddle" in backend.lower():
                    engine = "mobile"
                else:
                    continue
                for raw_text in evidence.get("ocr_texts") or []:
                    text = str(raw_text).strip()
                    strict = parse_date(text)
                    parsed = strict or parse_receipt_date(text, required)
                    if parsed is None or parsed.year != required.year:
                        continue
                    observations.append(
                        {
                            "date": parsed,
                            "strict": strict is not None,
                            "engine": engine,
                            "variant": variant,
                            "preprocessing": preprocessing,
                        }
                    )
    strict_server_dates = {
        item["date"]
        for item in observations
        if item["engine"] == "server" and item["strict"]
    }
    if len(strict_server_dates) != 1:
        return None
    candidate = next(iter(strict_server_dates))
    if abs((candidate - required).days) > 3:
        return None
    mobile_support = {
        (item["variant"], item["preprocessing"])
        for item in observations
        if item["engine"] == "mobile" and item["date"] == candidate
    }
    if {variant for variant, _ in mobile_support} != {"紧凑区域", "宽区域"} or len(
        mobile_support
    ) < 4:
        return None
    conflicts = [item for item in observations if item["date"] != candidate]
    conflict_dates = {item["date"] for item in conflicts}
    conflict_cells = {
        (item["engine"], item["variant"], item["preprocessing"]) for item in conflicts
    }
    if (
        len(conflict_dates) > 1
        or len(conflict_cells) > 1
        or any(item["engine"] != "mobile" for item in conflicts)
        or any(item["strict"] for item in conflicts)
    ):
        return None
    return candidate


def _repeated_server_required_date_from_artifacts(
    artifacts: list[dict], required_text: str
) -> date | None:
    """Confirm the required date from three strict Server transformations.

    This narrow fallback is used only when every parseable primary-crop date
    equals the printed requirement and Server reads that literal complete date
    in at least three differently preprocessed views of one geometry.  One or
    two views, repaired/partial dates, any conflict, and lower audit crops are
    rejected.
    """
    required = parse_date(required_text)
    if required is None:
        return None
    parsed_dates: set[date] = set()
    server_support: dict[str, set[str]] = {}
    for artifact in artifacts:
        variant = str(artifact.get("variant", ""))
        if variant not in {"紧凑区域", "宽区域"}:
            continue
        for key, default_backend in (
            ("ocr_variants", str(artifact.get("ocr_backend", ""))),
            (
                "secondary_ocr_variants",
                str(artifact.get("secondary_ocr_backend", "")),
            ),
            (
                "date_line_ocr_variants",
                str(artifact.get("date_line_ocr_backend", "")),
            ),
        ):
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                backend = default_backend
                is_server = (
                    "Server" in preprocessing
                    or "大模型" in preprocessing
                    or "server" in backend.lower()
                    or "大模型" in backend
                )
                for raw_text in evidence.get("ocr_texts") or []:
                    text = str(raw_text).strip()
                    strict = parse_date(text)
                    parsed = strict or parse_receipt_date(text, required)
                    if parsed is None:
                        continue
                    parsed_dates.add(parsed)
                    if is_server and strict == required:
                        server_support.setdefault(variant, set()).add(preprocessing)
    if parsed_dates != {required}:
        return None
    if not any(len(labels) >= 3 for labels in server_support.values()):
        return None
    return required


def _cross_year_strict_consensus_from_artifacts(
    artifacts: list[dict], required_text: str
) -> dict | None:
    """Return one adjacent-year date backed by both Paddle models/crops.

    A missing/partial year is routinely repaired with the printed required
    year, so it must never veto a literal four-digit year by itself. Promotion
    is nevertheless exceptional: Paddle Mobile and Paddle Server must each
    read the same sole strict date in both tight and wide crops, and every
    other parseable fragment must retain that same month/day. Vision is kept as
    optional corroboration because it can be unavailable in a macOS sandbox;
    a Windows single-Mobile route cannot satisfy this gate. No truth value is
    used by this selector.
    """
    required = parse_date(required_text)
    if required is None:
        return None
    primary_variants = {"紧凑区域", "宽区域"}
    required_engines = {"mobile", "server"}
    observations: list[dict] = []
    for artifact in artifacts:
        variant = str(artifact.get("variant", ""))
        if variant not in primary_variants:
            continue
        groups = (
            ("ocr_variants", str(artifact.get("ocr_backend", ""))),
            (
                "secondary_ocr_variants",
                str(artifact.get("secondary_ocr_backend", "")),
            ),
            (
                "date_line_ocr_variants",
                str(artifact.get("date_line_ocr_backend", "")),
            ),
        )
        for key, backend in groups:
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                label = f"{backend} {preprocessing}".lower()
                if "vision" in label:
                    engine = "vision"
                elif "server" in label or "大模型" in label:
                    engine = "server"
                elif "paddle" in label or "mobile" in label:
                    engine = "mobile"
                else:
                    continue
                for raw_text in evidence.get("ocr_texts") or []:
                    text = str(raw_text).strip()
                    strict = parse_date(text)
                    parsed = strict or parse_receipt_date(text, required)
                    if parsed is None:
                        continue
                    observations.append(
                        {
                            "date": parsed,
                            "strict": strict is not None,
                            "engine": engine,
                            "variant": variant,
                        }
                    )
    strict_dates = {item["date"] for item in observations if item["strict"]}
    if len(strict_dates) != 1:
        return None
    candidate = next(iter(strict_dates))
    if candidate.year == required.year or abs(candidate.year - required.year) != 1:
        return None
    support = {
        (item["engine"], item["variant"])
        for item in observations
        if item["strict"] and item["date"] == candidate
    }
    required_support = {
        (engine, variant) for engine in required_engines for variant in primary_variants
    }
    if not required_support.issubset(support):
        return None
    if any(
        (item["date"].month, item["date"].day) != (candidate.month, candidate.day)
        for item in observations
    ):
        return None
    return {
        "date": candidate,
        "support": [
            {"engine": engine, "variant": variant}
            for engine, variant in sorted(support)
        ],
        "strict_observation_count": sum(
            bool(item["strict"] and item["date"] == candidate) for item in observations
        ),
    }


def _missing_year_separator_consensus_from_artifacts(
    artifacts: list[dict],
    required_text: str,
) -> dict | None:
    """Select one OCR-owned required date before day-slot confirmation.

    Mobile must expose a self-contained ``YYYYM月D日`` reading and Server must
    expose the same strict ``YYYY年M月D日`` value in a normal tight/wide date
    row. The candidate must equal the printed requirement only after it has
    been independently assembled. Every other parseable date may only reduce
    its two-digit day to one printed digit while preserving year and month.
    """
    required = parse_date(required_text)
    if required is None or required.day < 10:
        return None
    primary = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if primary is None or (
        "vision" not in str(primary.get("ocr_backend", "")).lower()
        or "paddle" not in str(primary.get("secondary_ocr_backend", "")).lower()
    ):
        return None
    mobile: dict[date, list[dict]] = {}
    server: dict[date, list[dict]] = {}
    all_dates: set[date] = set()
    for artifact in artifacts:
        if artifact.get("variant") not in {"紧凑区域", "宽区域"}:
            continue
        default_backend = str(artifact.get("date_line_ocr_backend", ""))
        for evidence in artifact.get("date_line_ocr_variants") or []:
            preprocessing = str(evidence.get("preprocessing", ""))
            engine = (
                "server"
                if "Server" in preprocessing
                else "mobile" if "paddle" in default_backend.lower() else ""
            )
            for raw in evidence.get("ocr_texts") or []:
                text = str(raw)
                parsed = parse_date(text) or parse_receipt_date(text, required)
                if parsed is not None:
                    all_dates.add(parsed)
                if engine == "mobile":
                    candidate = _parse_missing_year_separator_full_date(text)
                    if candidate is not None:
                        mobile.setdefault(candidate, []).append(
                            {
                                "variant": artifact.get("variant", ""),
                                "preprocessing": preprocessing,
                                "text": text,
                            }
                        )
                elif engine == "server":
                    candidate = parse_date(text)
                    if candidate is not None:
                        server.setdefault(candidate, []).append(
                            {
                                "variant": artifact.get("variant", ""),
                                "preprocessing": preprocessing,
                                "text": text,
                            }
                        )
    common = set(mobile) & set(server)
    if common != {required}:
        return None
    truncated_dates = {
        date(required.year, required.month, value)
        for value in {required.day // 10, required.day % 10}
        if value >= 1
    }
    if not (all_dates - {required}) <= truncated_dates:
        return None
    return {
        "date": required,
        "mobile": mobile[required],
        "server": server[required],
        "other_dates": sorted(value.isoformat() for value in all_dates - {required}),
    }


def _cross_year_nondestructive_consensus_from_artifacts(
    artifacts: list[dict],
    required_text: str,
) -> dict | None:
    """Resolve one next-year date from literal, non-destructive OCR cells.

    The candidate is assembled before consulting the printed requirement.
    Mobile must return one strict date in four cells (crop/original line and
    their color-cleaned counterparts) in *both* tight and wide geometries.
    Server must return that same date in every available wide audit cell while
    every available tight cell returns the requirement. At least one Server
    cell is mandatory on each geometry. This intentionally handles a crop
    boundary disagreement and never treats destructive preprocessing votes as
    independent evidence.
    """
    required = parse_date(required_text)
    if required is None:
        return None
    by_variant = {
        str(item.get("variant", "")): item
        for item in artifacts
        if item.get("variant") in {"紧凑区域", "宽区域"}
    }
    tight = by_variant.get("紧凑区域")
    wide = by_variant.get("宽区域")
    if (
        tight is None
        or wide is None
        or (
            "vision" not in str(tight.get("ocr_backend", "")).lower()
            or "paddle" not in str(tight.get("secondary_ocr_backend", "")).lower()
        )
    ):
        return None

    def strict_values(variant: dict, key: str, preprocessing: str) -> set[date]:
        values: set[date] = set()
        for evidence in variant.get(key) or []:
            if str(evidence.get("preprocessing", "")) != preprocessing:
                continue
            for text in evidence.get("ocr_texts") or []:
                parsed = parse_date(str(text))
                if parsed is not None:
                    values.add(parsed)
        return values

    mobile_cells: dict[str, set[date]] = {}
    for geometry, artifact in (("tight", tight), ("wide", wide)):
        for key, preprocessing, label in (
            ("secondary_ocr_variants", "原始裁剪", "crop_original"),
            ("secondary_ocr_variants", "去印章色", "crop_color_clean"),
            ("date_line_ocr_variants", "日期行原图", "line_original"),
            (
                "date_line_ocr_variants",
                "日期行去印章色",
                "line_color_clean",
            ),
        ):
            mobile_cells[f"{geometry}_{label}"] = strict_values(
                artifact, key, preprocessing
            )
    if any(len(values) != 1 for values in mobile_cells.values()):
        return None
    mobile_common = set.intersection(*mobile_cells.values())
    if len(mobile_common) != 1:
        return None
    candidate = next(iter(mobile_common))

    server_preprocessings = (
        "日期行 Server 大模型复核不一致日期",
        "日期行 Server 大模型低置信度候选",
    )
    wide_server_cells = {
        name: strict_values(wide, "date_line_ocr_variants", name)
        for name in server_preprocessings
    }
    tight_server_cells = {
        name: strict_values(tight, "date_line_ocr_variants", name)
        for name in server_preprocessings
    }
    available_wide_server = {
        key: values for key, values in wide_server_cells.items() if values
    }
    available_tight_server = {
        key: values for key, values in tight_server_cells.items() if values
    }
    if not available_wide_server or any(
        values != {candidate} for values in available_wide_server.values()
    ):
        return None
    if not available_tight_server or any(
        values != {required} for values in available_tight_server.values()
    ):
        return None

    # The business date is a final boundary check, never a component source.
    if not (
        candidate.year == required.year + 1
        and candidate.month == required.month
        and candidate.day == required.day
    ):
        return None

    all_dates: list[dict] = []
    for geometry, artifact in (("紧凑区域", tight), ("宽区域", wide)):
        for key in (
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                engine = "server" if "Server" in preprocessing else "mobile"
                for text in evidence.get("ocr_texts") or []:
                    parsed = parse_date(str(text))
                    if parsed is None:
                        continue
                    all_dates.append(
                        {
                            "value": parsed,
                            "geometry": geometry,
                            "engine": engine,
                            "preprocessing": preprocessing,
                            "text": str(text),
                        }
                    )
    other_dates = {item["value"] for item in all_dates} - {candidate}
    truncated_day = (
        date(candidate.year, candidate.month, candidate.day % 10)
        if candidate.day >= 10 and candidate.day % 10
        else None
    )
    for other in other_dates:
        same_month_day_in_decade = (
            other.month == candidate.month
            and other.day == candidate.day
            and 2020 <= other.year <= 2029
        )
        if other not in {required, truncated_day} and not same_month_day_in_decade:
            return None
    return {
        "date": candidate,
        "support": {
            "candidate": candidate.isoformat(),
            "mobile_cells": {
                key: sorted(value.isoformat() for value in values)
                for key, values in mobile_cells.items()
            },
            "wide_server_cells": {
                key: sorted(value.isoformat() for value in values)
                for key, values in available_wide_server.items()
            },
            "tight_server_conflict_cells": {
                key: sorted(value.isoformat() for value in values)
                for key, values in available_tight_server.items()
            },
            "other_dates": sorted(value.isoformat() for value in other_dates),
        },
    }


def _server_cross_geometry_strict_date_from_artifacts(
    artifacts: list[dict],
) -> date | None:
    """Return one literal Server date repeated on tight and wide rows.

    The candidate comes only from the Server maximum-channel OCR text.  This
    prefilter never consults the printed required-delivery date and rejects
    any other literal four-digit date in decision-grade saved evidence.
    Variants explicitly labelled as low-confidence/manual-audit candidates
    remain visible, but cannot veto stronger cross-geometry evidence by
    themselves.
    """
    expected_preprocessing = "日期行最大通道去彩色三倍放大 Server 跨几何复核"
    by_geometry: dict[str, set[date]] = {}
    literal_dates: set[date] = set()
    for artifact in artifacts:
        geometry = str(artifact.get("variant", ""))
        for key in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for variant in artifact.get(key) or []:
                parsed_values = {
                    parsed
                    for text in variant.get("ocr_texts") or []
                    if (parsed := parse_date(str(text))) is not None
                }
                preprocessing = str(variant.get("preprocessing", ""))
                if not _is_date_audit_only_preprocessing(preprocessing):
                    literal_dates.update(parsed_values)
                if (
                    key == "date_line_ocr_variants"
                    and preprocessing == expected_preprocessing
                ):
                    by_geometry.setdefault(geometry, set()).update(parsed_values)
    if set(by_geometry) < {"紧凑区域", "宽区域"}:
        return None
    tight = by_geometry["紧凑区域"]
    wide = by_geometry["宽区域"]
    if len(tight) != 1 or tight != wide:
        return None
    candidate = next(iter(tight))
    return candidate if literal_dates == {candidate} else None


def _single_server_strict_truncated_day_candidate(
    artifacts: list[dict],
) -> date | None:
    """Find one full Server date backed by matching truncated day rows.

    This prefilter is intentionally independent of the requested-delivery
    date. A literal Server date must be unique, the maximum-channel rows from
    both geometries may differ only by losing one digit of its two-digit day,
    and Mobile must show that same truncation in both geometries. Finally,
    Server must preserve the full month/day in two additional partial-year
    preprocessing observations. The caller still has to confirm the isolated
    day with both OCR models on two image representations.
    """
    primary = {"紧凑区域", "宽区域"}
    server_label = "日期行最大通道去彩色三倍放大 Server 跨几何复核"
    mobile_label = "日期行最大通道去彩色三倍放大 Mobile 跨几何复核"
    literal_dates: set[date] = set()
    server_cells: dict[str, list[str]] = {}
    mobile_cells: dict[str, list[str]] = {}
    for artifact in artifacts:
        geometry = str(artifact.get("variant") or "")
        if geometry not in primary:
            continue
        for key in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for variant in artifact.get(key) or []:
                preprocessing = str(variant.get("preprocessing") or "")
                texts = [str(value) for value in variant.get("ocr_texts") or []]
                if not _is_date_audit_only_preprocessing(preprocessing):
                    literal_dates.update(
                        parsed
                        for text in texts
                        if (parsed := parse_date(text)) is not None
                    )
                if key != "date_line_ocr_variants":
                    continue
                if preprocessing == server_label:
                    server_cells[geometry] = texts
                elif preprocessing == mobile_label:
                    mobile_cells[geometry] = texts
    if len(literal_dates) != 1:
        return None
    candidate = next(iter(literal_dates))
    if candidate.day < 10:
        return None

    def truncated(text: str) -> bool:
        match = re.fullmatch(
            r"(\d{4})年(\d{1,2})月(\d)日",
            re.sub(r"\s+", "", text),
        )
        return bool(
            match
            and int(match.group(1)) == candidate.year
            and int(match.group(2)) == candidate.month
            and int(match.group(3)) in {candidate.day // 10, candidate.day % 10}
        )

    if set(server_cells) != primary or set(mobile_cells) != primary:
        return None
    if not all(
        any(parse_date(text) == candidate or truncated(text) for text in texts)
        for texts in server_cells.values()
    ):
        return None
    if not all(
        any(truncated(text) for text in texts) for texts in mobile_cells.values()
    ):
        return None

    partial_support: set[tuple[str, str]] = set()
    for artifact in artifacts:
        geometry = str(artifact.get("variant") or "")
        if geometry not in primary:
            continue
        for variant in artifact.get("date_line_ocr_variants") or []:
            preprocessing = str(variant.get("preprocessing") or "")
            if "Server" not in preprocessing:
                continue
            for text in variant.get("ocr_texts") or []:
                match = re.fullmatch(
                    r"(\d{3})年(\d{1,2})月(\d{1,2})日",
                    re.sub(r"\s+", "", str(text)),
                )
                if (
                    match
                    and str(candidate.year).startswith(match.group(1))
                    and int(match.group(2)) == candidate.month
                    and int(match.group(3)) == candidate.day
                ):
                    partial_support.add((geometry, preprocessing))
    return candidate if len(partial_support) >= 2 else None
