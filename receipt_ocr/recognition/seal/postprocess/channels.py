"""Let the local pass vote when it read inside QingTong's own stamp box.

``seal_provider_policy`` keeps local fuzzy/geometry readings out of provider
arbitration on purpose: a stamp region guessed from the footer is weaker
evidence than an API call that read the page itself.  That reasoning stops
holding once the local pass no longer guesses — it re-reads exactly the pixels
the API judged.  A full match there is one more independent observation of the
*same* stamp, so it counts as a channel of its own.

Scope of the vote:

* Only a reading marked ``region_source = 清瞳印章区域`` qualifies.  A local pass
  that detected its own region stays evidence-only, whatever it read.
* Only ``any`` mode lets it decide.  Under ``all`` the question is whether every
  API channel agrees; a local re-read cannot make a disagreeing API channel
  agree, and counting it would only turn previously passing records into manual
  review.  The reading is still recorded for the reviewer.
* A partial or mismatching local reading never votes.  The bar is the same exact
  match the API's own channels are held to.
"""

from __future__ import annotations

from copy import deepcopy

# Written by the seal stage when QingTong supplied the region.
LOCAL_REGION_SOURCE = "清瞳印章区域"
# Shown as ``seal_check['source']`` when this channel decides the verdict.
LOCAL_CHANNEL_SOURCE = "本地识别（清瞳印章区域）"
LOCAL_CHANNEL_MESSAGE = (
    "本地识别在清瞳给出的印章区域内读到完整符合签章要求的文字，判定匹配"
)
LOCAL_CHANNEL_ALL_NOTE = "全部结果匹配模式下，本地通道仅作为证据，不参与判定"

# Channel keys copied out of the local reading when it decides the verdict.
_CHANNEL_KEYS = (
    "recognized",
    "score",
    "confidence",
    "requirement_coverage",
    "all_recognized",
    "display_text",
)


def local_seal_full_match(check) -> bool:
    """True when a local reading taken inside QingTong's box fully matches."""
    return bool(
        isinstance(check, dict)
        and check.get("region_source") == LOCAL_REGION_SOURCE
        and check.get("status") == "匹配"
        and check.get("reliable") is True
        and not check.get("simulated")
    )


def record_local_channel(verdict: dict, local_check, match_mode: str = "any") -> dict:
    """Attach the local channel, and let it decide the verdict under ``any``.

    Returns the same dict, mutated in place, so callers can chain it onto an
    existing seal check without threading a new return value.
    """
    if not isinstance(local_check, dict) or not local_check:
        return verdict
    verdict = dict(verdict)
    verdict["local_channel"] = deepcopy(local_check)
    if not local_seal_full_match(local_check):
        return verdict
    if match_mode == "all":
        verdict["local_channel_note"] = LOCAL_CHANNEL_ALL_NOTE
        return verdict
    if verdict.get("status") == "匹配":
        # The API already agreed; keep its reading as the displayed one.
        return verdict
    for key in _CHANNEL_KEYS:
        if key in local_check and local_check[key] is not None:
            verdict[key] = deepcopy(local_check[key])
    verdict["all_recognized"] = [
        text
        for text in dict.fromkeys(
            [
                *(verdict.get("all_recognized") or []),
                *(local_check.get("all_recognized") or []),
                local_check.get("recognized") or "",
            ]
        )
        if text
    ]
    verdict.update(
        status="匹配",
        reliable=True,
        source=LOCAL_CHANNEL_SOURCE,
        message=LOCAL_CHANNEL_MESSAGE,
    )
    return verdict
