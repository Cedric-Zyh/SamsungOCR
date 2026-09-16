"""Seal-text comparison with explicit company, station and stamp-type evidence."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .parsing_text import normalize_text


def normalize_seal_text_strict(text: str) -> str:
    """Ignore OCR spacing, parentheses and full-width ASCII typography.

    Do not use the general receipt normalizer: it drops other punctuation,
    which could hide a wrong character or change a station identifier.
    Restrict width conversion to ASCII forms so compatibility normalization
    cannot silently equate different Chinese characters or numeric symbols.
    """
    return "".join(
        chr(ord(character) - 0xFEE0) if "\uff01" <= character <= "\uff5e" else character
        for character in text
        if not character.isspace() and character not in "()（）"
    )


def _strict_seal_text_status(expected: str, actual: str) -> str:
    if not expected:
        return "无法判断"
    if not actual:
        return "未识别"
    if actual == expected:
        return "匹配"
    # A partial result may omit any expected glyphs, including internal ones,
    # but every glyph it does return must occur in the original order.
    remaining = iter(expected)
    if len(actual) < len(expected) and all(character in remaining for character in actual):
        return "部分匹配"
    return "不匹配"


def _required_character_coverage(expected: str, actual: str) -> float:
    """Measure exact in-order required glyphs with a longest common subsequence."""
    if not expected:
        return 0.0
    previous = [0] * (len(actual) + 1)
    for expected_character in expected:
        current = [0]
        for index, actual_character in enumerate(actual, 1):
            current.append(
                previous[index - 1] + 1 if expected_character == actual_character
                else max(previous[index], current[index - 1])
            )
        previous = current
    return previous[-1] / len(expected)


def seal_text_comparison_rank(comparison: dict) -> tuple[int, float, float]:
    """Rank evidence by business meaning before raw text similarity."""
    return (
        {"匹配": 3, "部分匹配": 2, "不匹配": 1}.get(comparison.get("status"), 0),
        comparison.get("requirement_coverage", 0.0),
        comparison.get("score", 0.0),
    )


def compare_seal_text_strict(requirement: str, recognized_texts: list[str]) -> dict:
    """Separate exact text, omission-only partial text and incorrect text."""
    expected = normalize_seal_text_strict(requirement)
    candidates = []
    for text in recognized_texts:
        actual = normalize_seal_text_strict(text)
        if not actual:
            continue
        candidates.append({
            "recognized": text,
            "status": _strict_seal_text_status(expected, actual),
            "requirement_coverage": _required_character_coverage(expected, actual),
            "score": SequenceMatcher(None, expected, actual).ratio() if expected else 0.0,
        })
    selected = max(
        candidates,
        key=seal_text_comparison_rank,
        default={"recognized": "", "status": "未识别" if expected else "无法判断",
                 "requirement_coverage": 0.0, "score": 0.0},
    )
    best_text, status, score = selected["recognized"], selected["status"], selected["score"]
    message = {
        "无法判断": "未识别到签章要求",
        "未识别": "检测到的收货章中未识别出文字",
        "匹配": "收货章内容与签章要求一致",
        "部分匹配": "印章已识别文字与签章要求顺序一致，但存在漏字，需人工复核",
        "不匹配": "收货章内容与签章要求存在错字、多字或顺序差异",
    }[status]
    return {
        "requirement": requirement,
        "recognized": best_text,
        "all_recognized": recognized_texts,
        "score": round(score, 3),
        "requirement_coverage": round(selected["requirement_coverage"], 6),
        "status": status,
        "message": message,
        "confidence": round(score, 3),
        "company_score": round(score, 3),
        "company_conflict": False,
        "reliable": status in {"匹配", "不匹配"},
        "comparison_policy": "strict_text",
    }


def compare_seal_text(requirement: str, recognized_texts: list[str]) -> dict:
    expected = normalize_text(requirement)
    expected_company = _company_name(expected)
    expected_company_core = _company_core(expected_company)
    normalized_recognized = [
        normalize_text(text) for text in recognized_texts if normalize_text(text)
    ]
    company_conflict = _has_conflicting_complete_company(
        expected_company, normalized_recognized
    )
    company_marker_present = "有限公司" in expected_company
    company_only_requirement = bool(
        company_marker_present and expected == expected_company
    )
    # One or two OCR glyph errors are common on curved company names.  The
    # threshold still rejects the known different-company sample (0.625),
    # while retaining true names with one missing/substituted glyph.
    company_threshold = 0.70
    best_text, best_score = "", 0.0
    best_reliable_text, best_reliable_score = "", 0.0
    best_company_score = 0.0
    for text in recognized_texts:
        actual = normalize_text(text)
        if not actual:
            continue
        sequence = SequenceMatcher(None, expected, actual).ratio()
        company_sequence = _partial_similarity(expected_company, actual) if expected_company else 0.0
        expected_chars, actual_chars = set(expected), set(actual)
        coverage = len(expected_chars & actual_chars) / max(1, len(expected_chars))
        company_core_score = (
            _partial_similarity(expected_company_core, actual)
            if expected_company_core else 0.0
        )
        # Circular stamps may be unwrapped from the opposite side of the
        # ring, so a two-character place prefix can appear reversed (南昌 →
        # 昌南) while the distinctive organization core is read elsewhere in
        # the same region. Treat this as corroborated company evidence only
        # when the remaining core is long and independently matches well.
        circular_prefix_match = _circular_prefix_company_match(
            expected_company_core, actual
        )
        if circular_prefix_match:
            company_core_score = max(company_core_score, 0.88)
        # Exact containment is strong only when the recognized substring covers
        # most of the requirement.  A station number or short stamp suffix can
        # also be a literal substring, but must never receive a perfect score.
        contains_bonus = 0.0
        if expected in actual:
            contains_bonus = 1.0
        elif actual in expected and len(actual) >= max(8, int(len(expected) * 0.75)):
            contains_bonus = 1.0
        station_stamp_match = _matching_station_stamp(requirement, text)
        samsung_service_stamp_match = _matching_samsung_service_stamp(
            requirement, text
        )
        score = max(
            sequence, company_sequence * 0.96, coverage * 0.9,
            contains_bonus, 0.88 if circular_prefix_match else 0.0,
            0.94 if station_stamp_match else 0.0,
            0.90 if samsung_service_stamp_match else 0.0,
        )
        # A company-only requirement has no separate stamp-type suffix to
        # recover.  Accept the lower raw boundary only when the legal marker
        # (or its common one-glyph-clipped form) survives and the distinctive company core is independently
        # strong.  This keeps generic ``有限公司`` overlap from hiding another
        # organization while rescuing fragmented curved-company OCR.
        company_only_match = bool(
            company_only_requirement
            and ("有限公司" in actual or "限公司" in actual)
            and company_core_score >= 0.82
            and score >= 0.72
        )
        if score > best_score:
            best_text, best_score, best_company_score = text, score, company_core_score
        if (
            (
                score >= 0.78
                or station_stamp_match
                or samsung_service_stamp_match
                or company_only_match
            )
            and (not company_marker_present or company_core_score >= company_threshold)
            and _has_required_suffix(requirement, text)
            and not company_conflict
            and score > best_reliable_score
        ):
            best_reliable_text, best_reliable_score = text, score

    # A reviewed Samsung local-service round stamp places the brand, city and
    # ``服务中心`` around different arcs.  Color-only Server transforms can
    # expose them in one same-region aggregate without ever producing the
    # original circular order.  Accept only this exact template structure and
    # the observed 孝/考 OCR confusion; do not grant generic one-character
    # city fuzziness that could confuse two genuinely different service sites.
    fragmented_local_service_text = next(
        (
            text for text in recognized_texts
            if _matching_fragmented_local_samsung_service_stamp(
                requirement, text
            )
        ),
        "",
    )
    if fragmented_local_service_text and not company_conflict:
        best_reliable_text = fragmented_local_service_text
        best_reliable_score = max(best_reliable_score, 0.90)

    # A company-only fragment can have the highest raw similarity while a
    # slightly noisier same-region aggregate also contains the required stamp
    # type.  Prefer the latter as the displayed/verifying evidence because it
    # satisfies both parts of the business requirement.
    if best_reliable_text:
        best_text, best_score = best_reliable_text, best_reliable_score
        best_company_score = _partial_similarity(
            expected_company_core, normalize_text(best_reliable_text)
        )
        if _circular_prefix_company_match(
            expected_company_core, normalize_text(best_reliable_text)
        ):
            best_company_score = max(best_company_score, 0.88)

    if not requirement:
        status, message = "无法判断", "未识别到签章要求"
    elif not best_text:
        status, message = "未识别", "检测到的收货章中未识别出文字"
    elif best_score >= 0.72 and best_reliable_text:
        status, message = "匹配", "收货章内容与签章要求一致"
    elif best_score >= 0.72:
        status, message = (
            ("无法判断", "识别到完整但不同的公司全称，禁止模糊判定通过")
            if company_conflict else
            ("无法判断", "印章存在相似文字，但公司名或章类型证据不足")
        )
    else:
        status, message = "不匹配", "收货章内容与签章要求不一致"
    return {
        "requirement": requirement,
        "recognized": best_text,
        "all_recognized": recognized_texts,
        "score": round(best_score, 3),
        "status": status,
        "message": message,
        "confidence": round(best_score, 3),
        "company_score": round(best_company_score, 3),
        "company_conflict": company_conflict,
        "reliable": bool(best_reliable_text),
    }


def _matching_station_stamp(requirement: str, actual: str) -> bool:
    """Require both a specific pickup-stamp type and its exact station id."""
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    if "取机专用章" not in expected or "取机专用章" not in recognized:
        return False
    # Search before stripping parentheses: ``（2）6237143站`` must not turn
    # into the artificial combined number ``26237143站``.
    station = re.search(r"(\d{6,})\s*站", requirement)
    actual_compact = re.sub(r"\s", "", actual)
    return bool(station and f"{station.group(1)}站" in actual_compact)


def _matching_samsung_service_stamp(requirement: str, actual: str) -> bool:
    """Recognize the bilingual Samsung customer-service stamp safely.

    Some light red stamps expose the Latin brand and only the Chinese stamp
    type, while losing the curved ``三星电子客户服务中心`` text.  The three
    independent tokens below are specific to this reviewed stamp template;
    ``SAMSUNG`` alone or a generic service fragment is never sufficient.
    """
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    dedicated_service_stamp = bool(
        "三星电子" in expected
        and "客户服务中心" in expected
        and "服务专用章" in expected
        and "samsung" in recognized
        and "服务" in recognized
        and ("专用章" in recognized or "用章" in recognized)
    )
    # A second reviewed template prints the requirement itself as the
    # bilingual brand plus ``客户服务中心`` and has no ``专用章`` suffix.  Match
    # it only when the seal region independently exposes the Latin brand and
    # the complete Chinese ``服务中心`` type.  Either token alone remains too
    # generic for an automatic conclusion.
    bilingual_customer_center = bool(
        "三星" in expected
        and "samsung" in expected
        and "客户服务中心" in expected
        and "samsung" in recognized
        and "服务中心" in recognized
    )
    return dedicated_service_stamp or bilingual_customer_center


def _matching_fragmented_local_samsung_service_stamp(
    requirement: str, actual: str
) -> bool:
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    match = re.fullmatch(
        r"三星电子(?P<place>[\u4e00-\u9fff]{2,6})服务中心",
        expected,
    )
    if not match or len(recognized) > 80:
        return False
    place = match.group("place")
    place_seen = place in recognized or (
        place == "孝感" and "考感" in recognized
    )
    brand_seen = any(
        token in recognized for token in ("三星电子", "三星电", "星电子", "星电")
    )
    service_seen = "服务中" in recognized or (
        "服务" in recognized and "中心" in recognized
    )
    return bool(place_seen and brand_seen and service_seen)


def _has_required_suffix(requirement: str, actual: str) -> bool:
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    # Station/code identifiers are business keys, not fuzzy text.  A stamp
    # with one missing or substituted digit must stay in review even when the
    # surrounding ``三星售后``/company wording makes the overall similarity
    # exceed the normal threshold.  Telephone numbers elsewhere in the
    # requirement are deliberately excluded from this exact-key check.
    station_ids = re.findall(r"(\d{6,12})\s*站", requirement)
    code_ids = re.findall(
        r"(?:授权代码|站代码|代码)\s*[:：]?\s*(\d{6,12})",
        requirement,
    )
    if any(identifier not in recognized for identifier in station_ids + code_ids):
        return False
    if "业务受理" in expected:
        branch = re.search(r"业务受理(\d+)", expected)
        return bool(
            "业务受理" in recognized
            and (not branch or f"业务受理{branch.group(1)}" in recognized)
        )
    marker = "有限公司"
    index = expected.find(marker)
    if index < 0:
        return len(recognized) >= max(6, int(len(expected) * 0.6))
    suffix = expected[index + len(marker) :]
    if not suffix:
        return len(recognized) >= int(len(expected) * 0.7)
    # A color-only Server crop can read the full legal company and the
    # distinctive center row ``业务专用`` while clipping only the terminal
    # generic glyph ``章``.  Accept this narrowly: both the exact complete
    # company and all four type glyphs must occur in the same region-level
    # aggregate. Company-only evidence or a generic ``专用`` fragment remains
    # insufficient.
    if (
        "业务专用章" in suffix
        and expected[: index + len(marker)] in recognized
        and "业务专用" in recognized
    ):
        return True
    # If the required stamp type is explicit, at least one meaningful suffix token must survive OCR.
    tokens = [token for token in ("收货章", "专用章", "服务", "售后", "仓储部", "维修中心") if token in suffix]
    return not tokens or any(token in recognized for token in tokens)


def _company_name(text: str) -> str:
    marker = "有限公司"
    index = text.find(marker)
    return text[: index + len(marker)] if index >= 0 else text


def _company_core(text: str) -> str:
    """Remove generic legal suffixes before checking company-name evidence."""
    core = text
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    return core or text


def _has_conflicting_complete_company(
    expected_company: str, recognized_texts: list[str]
) -> bool:
    """Veto fuzzy matching when OCR exposes a complete near-name company.

    A real stamp from ``大连允华…有限公司`` differs from the required
    ``大连北华…有限公司`` by one distinctive glyph and can otherwise exceed a
    permissive curved-text similarity threshold.  If any transform also sees
    the exact expected company core, the alternate spelling is treated as an
    OCR error.  Without that corroboration, a complete similarly sized legal
    name is contradictory evidence and must stay in human review.
    """
    if not expected_company or "有限公司" not in expected_company:
        return False
    expected_core = _company_core(expected_company)
    exact_core_seen = bool(
        expected_core and any(expected_core in text for text in recognized_texts)
    )
    marker = "有限公司"
    expected_length = len(expected_company)
    for text in recognized_texts:
        # Aggregated same-region evidence intentionally concatenates several
        # transforms.  Sliding a company-length window across that long audit
        # string can manufacture a fake "alternate company" at a transform
        # boundary.  Conflict evidence must be an independently recognized
        # near-complete legal name, allowing only a short OCR prefix.
        cursor = 0
        while True:
            marker_index = text.find(marker, cursor)
            if marker_index < 0:
                break
            end = marker_index + len(marker)
            cursor = end
            # OCR can replace, add, or omit several glyphs in the distinctive
            # part of a legal company name.  Inspect a narrow length band
            # around the requirement instead of only an equal-length window;
            # otherwise ``十堰盛瑞…有限公司`` can incorrectly corroborate the
            # longer, different ``十堰市万盛达…有限公司`` requirement.
            minimum_length = max(len(marker) + 4, expected_length - 4)
            for candidate_length in range(minimum_length, expected_length + 5):
                if end < candidate_length:
                    continue
                start = end - candidate_length
                candidate = text[start:end]
                # An independent OCR row can legitimately contain the complete
                # legal company followed by a stamp type/branch number, e.g.
                # ``洛阳西利通信有限公司业务受理（2）``.  Judge the short
                # prefix/tail around the legal name.  Long transform aggregates
                # remain excluded because at least one side exceeds these
                # bounds or contains another legal-company marker.
                prefix = text[:start]
                suffix = text[end:]
                if (
                    len(prefix) > 4
                    or len(suffix) > 20
                    or marker in prefix
                    or marker in suffix
                ):
                    continue
                matcher = SequenceMatcher(None, expected_company, candidate)
                changed_glyphs = sum(
                    (expected_end - expected_start)
                    + (candidate_end - candidate_start)
                    for operation,
                    expected_start,
                    expected_end,
                    candidate_start,
                    candidate_end in matcher.get_opcodes()
                    if operation != "equal"
                )
                if (
                    candidate != expected_company
                    and matcher.ratio() >= 0.82
                    # A shorter observation with only one compact OCR wound is
                    # the established one-character tolerance case.  Require
                    # broader disagreement before treating unequal-length
                    # names as two different complete companies.  Equal-length
                    # near names keep the original stricter veto.
                    and (
                        candidate_length == expected_length
                        or changed_glyphs >= 4
                    )
                ):
                    # A partial or complete observation containing the expected
                    # distinctive company core can explain one alternate complete
                    # reading as an OCR spelling error.  Cross-model disagreement
                    # is preserved earlier in the analyzer: a later Server result
                    # is audit-only when regular evidence already raised this
                    # conflict, so it cannot manufacture this corroboration.
                    if exact_core_seen:
                        continue
                    return True
    return False


def _circular_prefix_company_match(expected_core: str, actual: str) -> bool:
    if len(expected_core) < 6:
        return False
    place_prefix = expected_core[:2]
    organization_core = expected_core[2:]
    return bool(
        place_prefix[::-1] in actual
        and _partial_similarity(organization_core, actual) >= 0.82
    )


def _partial_similarity(expected: str, actual: str) -> float:
    if not expected or not actual:
        return 0.0
    if len(actual) < max(6, int(len(expected) * 0.45)):
        return SequenceMatcher(None, expected, actual).ratio()
    if len(actual) < len(expected):
        return SequenceMatcher(None, expected, actual).ratio()
    if len(actual) - len(expected) > 120:
        return 0.0
    window = len(expected)
    candidates = [actual]
    if len(actual) > window:
        candidates.extend(actual[i : i + window] for i in range(len(actual) - window + 1))
    return max(SequenceMatcher(None, expected, candidate).ratio() for candidate in candidates)
