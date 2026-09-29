"""Tests for the provider-neutral optional secondary seal read."""
from __future__ import annotations

from pathlib import Path

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import reading_policy
from receipt_ocr.recognition.seal.contracts import OcrRead, RegionEvidence
from receipt_ocr.recognition.seal.ocr.interface import normalize_reads
from receipt_ocr.recognition.seal.ocr.providers import profile_for
from receipt_ocr.recognition.seal.reading_policy import (
    SKIP_DISABLED,
    SKIP_SAME_MODEL,
    SECONDARY_READ_MODES,
    describe_secondary_read,
    resolve_secondary_read,
    secondary_read_providers_for_plan,
)
from receipt_ocr.recognition.seal.preprocess.shapes import prepared_from_evidence


def test_unknown_secondary_mode_falls_back_to_auto(monkeypatch):
    monkeypatch.delenv("SEAL_SECONDARY_READ_MODE", raising=False)
    assert reading_policy.secondary_read_mode() == "auto"
    monkeypatch.setenv("SEAL_SECONDARY_READ_MODE", "unknown")
    assert reading_policy.secondary_read_mode() == "auto"


def test_all_secondary_modes_are_explicit(monkeypatch):
    for mode in SECONDARY_READ_MODES:
        monkeypatch.setenv("SEAL_SECONDARY_READ_MODE", mode)
        payload = describe_secondary_read()
        assert payload["mode"] == mode
        assert payload["modes"] == list(SECONDARY_READ_MODES)


def test_off_records_a_reason_without_selecting_a_provider(monkeypatch):
    monkeypatch.setenv("SEAL_SECONDARY_READ_MODE", "off")
    plan = resolve_secondary_read("vision", allowed=None)
    assert not plan.runs
    assert plan.skip_reason == SKIP_DISABLED
    assert plan.authorise == frozenset()


def test_auto_uses_local_reader_only_for_a_different_primary(monkeypatch):
    monkeypatch.setenv("SEAL_SECONDARY_READ_MODE", "auto")
    plan = resolve_secondary_read("vision", allowed=None)
    assert plan.runs
    assert plan.backend == "paddle_v6"
    assert plan.independent
    same = resolve_secondary_read("paddle_v6", allowed=None)
    assert not same.runs
    assert same.skip_reason == SKIP_SAME_MODEL.format(backend="paddle_v6")


def test_auto_does_not_widen_a_scoped_request(monkeypatch):
    monkeypatch.setenv("SEAL_SECONDARY_READ_MODE", "auto")
    plan = resolve_secondary_read("vision", allowed=frozenset({"vision"}))
    assert not plan.runs
    assert plan.authorise == frozenset()
    assert secondary_read_providers_for_plan({"seal": ["paddle_v6"]}) == frozenset()


def test_local_mode_authorizes_the_resolved_reader(monkeypatch):
    monkeypatch.setenv("SEAL_SECONDARY_READ_MODE", "local")
    plan = resolve_secondary_read("qingtong", allowed=frozenset({"qingtong"}))
    assert plan.runs
    assert plan.backend == "paddle_v6"
    assert plan.authorise == frozenset({"paddle_v6"})
    assert secondary_read_providers_for_plan({"seal": ["qingtong"]}) == frozenset({"paddle_v6"})


def test_ocr_profiles_describe_capabilities_without_workflow_branches():
    assert profile_for("paddle_v6").supports_lines
    assert profile_for("vision").supports_lines is False


def test_normalize_reads_keeps_provider_neutral_contract(tmp_path: Path):
    rows = [TextObservation(" 公司章 ", 0.91, 0, 0, 1, 1)]
    reads = normalize_reads(rows, provider="vision", variant="region", source=tmp_path / "seal.png")
    assert reads == [OcrRead(text=" 公司章 ", confidence=0.91, provider="vision", variant="region", source=tmp_path / "seal.png")]


def test_prepared_contract_contains_only_existing_variants(tmp_path: Path):
    original = tmp_path / "original.png"
    original.write_bytes(b"image")
    evidence = RegionEvidence(original=original)
    prepared = prepared_from_evidence(2, "round", evidence)
    assert prepared.index == 2
    assert prepared.shape == "round"
    assert [variant.name for variant in prepared.variants] == ["original"]
