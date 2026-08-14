from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ARTIFACT_MARKER = "/files/artifacts/"


def _resolve(url: str, artifact_root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = artifact_root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _font(size: int):
    for candidate in (
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def render_contact_sheet(
    report: dict,
    category: str,
    artifact_root: Path,
    output: Path,
    columns: int = 4,
) -> int:
    samples = [
        row for row in report.get("samples", [])
        if row.get("gap_category") == category
    ]
    card_width, image_height, label_height = 420, 150, 52
    rows = max(1, (len(samples) + columns - 1) // columns)
    sheet = Image.new(
        "RGB", (columns * card_width, rows * (image_height + label_height)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    title_font = _font(18)
    small_font = _font(14)
    rendered = 0
    for index, sample in enumerate(samples):
        candidate_paths = []
        for artifact in sample.get("date_artifact_urls") or []:
            for key in ("original_url", "color_clean_url", "table_clean_url"):
                path = _resolve(str(artifact.get(key, "")), artifact_root)
                if path is not None:
                    candidate_paths.append(path)
            if candidate_paths:
                break
        x = (index % columns) * card_width
        y = (index // columns) * (image_height + label_height)
        if candidate_paths:
            with Image.open(candidate_paths[0]) as source:
                image = ImageOps.contain(
                    source.convert("RGB"), (card_width - 20, image_height - 12)
                )
            image_x = x + (card_width - image.width) // 2
            image_y = y + (image_height - image.height) // 2
            sheet.paste(image, (image_x, image_y))
            rendered += 1
        draw.rectangle(
            (x, y, x + card_width - 1, y + image_height + label_height - 1),
            outline="#CBD5E1",
            width=1,
        )
        draw.text(
            (x + 8, y + image_height + 3),
            str(sample.get("filename", "")),
            fill="#0F172A",
            font=title_font,
        )
        draw.text(
            (x + 8, y + image_height + 26),
            f"要求 {sample.get('required', '')}  真值 {sample.get('truth', '')}",
            fill="#475569",
            font=small_font,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="生成日期差距样本联系表")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--columns", type=int, default=4)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    count = render_contact_sheet(
        report, args.category, args.artifact_root, args.output, args.columns
    )
    print(json.dumps({"category": args.category, "rendered": count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
