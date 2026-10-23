"""Pale lines may affect geometry, but must never erase faint glyph evidence."""
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import strong_border


def polluted_stamp(tmp_path, color="blue"):
    image = np.full((800, 1000, 3), 255, np.uint8)
    ink = (30, 30, 255) if color == "red" else (255, 30, 30)
    pale = (240, 240, 255) if color == "red" else (255, 240, 240)
    cv2.ellipse(image, (500, 400), (420, 280), 0, 0, 360, ink, 7)
    for index in range(3):
        cv2.line(image, (500 + index * 20, 20), (990, 670 + index * 20), pale, 3)
    image[240:250, 300:320] = pale  # A faint detached glyph stroke.
    image[450:470, 400:600] = ink  # The separately detected centre text.
    masked = image.copy()
    masked[450:470, 400:600] = 255
    intact_path, masked_path = tmp_path / "intact.png", tmp_path / "masked.png"
    cv2.imwrite(str(intact_path), image)
    cv2.imwrite(str(masked_path), masked)
    return intact_path, masked_path


def reader(texts=("观测到的公司名", "观测到的公司名"), scores=(.98, .99)):
    calls = []
    def read(path, **kwargs):
        index = len(calls)
        calls.append(path)
        return [SimpleNamespace(text=texts[index], confidence=scores[index])]
    return SimpleNamespace(read_line=read, calls=calls)


@pytest.mark.parametrize("color", ["red", "blue"])
def test_real_geometry_ignores_pale_lines_and_keeps_original_ocr_pixels(tmp_path, monkeypatch, color):
    working, masked = polluted_stamp(tmp_path, color)
    originals = [path.read_bytes() for path in (working, masked)]
    save = strong_border._save_ellipse_annulus_unwrapped
    def inspect_pixels(gray, mask, destination, **kwargs):
        assert 0 < gray[245, 310] < 255  # Faint ink remains eligible for OCR.
        assert np.all(gray[450:470, 400:600] == 255)  # Centre mask stays exact.
        return save(gray, mask, destination, **kwargs)
    monkeypatch.setattr(strong_border, "_save_ellipse_annulus_unwrapped", inspect_pixels)
    result = strong_border.prepare_strong_border_ring(
        working, masked, tmp_path / "strong.png", color=color, reader=reader(), provider="paddle_v6")

    assert result is not None
    assert np.linalg.norm(np.subtract(result["guide"].annulus[0][2][0], (500, 400))) < 2
    assert result["ring"].is_file() and result["check_ring"].is_file()
    assert result["border"].is_file()
    assert result["evidence"]["status"] == "verified"
    assert [path.read_bytes() for path in (working, masked)] == originals


@pytest.mark.parametrize("texts,scores", [
    (("恒信公司", "有信公司"), (.99, .999)),
    (("恒信公司", "恒信公司"), (.969, .999)),
    (("恒信公司", "恒信公司"), (float("nan"), .99)),
    (("恒信公司", "恒信公司"), (float("inf"), .99)),
    (("恒信公司", "恒信公司"), (1.01, .99)),
    (("", ""), (1, 1)),
])
def test_disagreeing_or_weak_views_never_replace_the_existing_ring(tmp_path, texts, scores):
    working, masked = polluted_stamp(tmp_path)
    assert strong_border.prepare_strong_border_ring(
        working, masked, tmp_path / "strong.png", color="blue",
        reader=reader(texts, scores), provider="paddle_v6") is None


@pytest.mark.parametrize("reason", ["blank", "clean_border", "wrong_frame", "unsupported_color"])
def test_unneeded_or_unsafe_geometry_does_not_run_ocr(tmp_path, reason):
    working, masked = polluted_stamp(tmp_path)
    if reason in {"blank", "clean_border"}:
        image = np.full((800, 1000, 3), 255, np.uint8)
        if reason == "clean_border":
            cv2.ellipse(image, (500, 400), (420, 280), 0, 0, 360, (255, 30, 30), 7)
        cv2.imwrite(str(working), image)
        cv2.imwrite(str(masked), image)
    elif reason == "wrong_frame":
        cv2.imwrite(str(masked), np.full((40, 60, 3), 255, np.uint8))
    ocr = Mock()
    assert strong_border.prepare_strong_border_ring(
        working, masked, tmp_path / "strong.png",
        color="black" if reason == "unsupported_color" else "blue", reader=ocr, provider="paddle_v6") is None
    assert ocr.mock_calls == []


@pytest.mark.parametrize("reason", ["center", "axes", "residual", "missing"])
def test_unstable_or_inaccurate_ellipse_is_rejected_before_ocr(tmp_path, monkeypatch, reason):
    working, masked = polluted_stamp(tmp_path)
    fits, _, _ = strong_border._stable_fits(cv2.imread(str(working)), "blue")
    first, second = fits
    if reason == "center":
        shape = ((550., 400.), second[0][2][1], second[0][2][2])
        second = ((second[0][0], second[0][1], shape), second[1], second[2])
    elif reason == "axes":
        shape = (second[0][2][0], (500., 900.), second[0][2][2])
        second = ((second[0][0], second[0][1], shape), second[1], second[2])
    elif reason == "residual":
        second = ((second[0][0], .04, second[0][2]), second[1], second[2])
    else:
        second = None
    base = ((first[0][0], .08, first[0][2]), first[1], first[2])
    sequence = iter((base, first, second))
    monkeypatch.setattr(strong_border, "_fit_ellipse_annulus", lambda _: next(sequence))
    ocr = Mock()
    assert strong_border.prepare_strong_border_ring(
        working, masked, tmp_path / "strong.png", color="blue", reader=ocr, provider="paddle_v6") is None
    assert ocr.mock_calls == []
