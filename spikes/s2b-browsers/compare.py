"""Diff the frozen 1080p captures against Chromium and make a contact sheet."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

PAGES = ("static", "slides", "chart", "step")


def metrics(reference, image):
    difference = ImageChops.difference(reference, image)
    maximum = ImageChops.lighter(ImageChops.lighter(*difference.split()[:2]), difference.getchannel("B"))
    histogram = maximum.histogram()
    total = reference.width * reference.height
    return {
        "mae_rgb_0_to_255": round(sum(ImageStat.Stat(difference).mean) / 3, 2),
        "pct_any_channel_diff_over_16": round(100 * sum(histogram[17:]) / total, 2),
    }, maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("engines", nargs="+")
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--width", type=int, default=360)
    args = parser.parse_args()
    width = args.width
    height = width * 9 // 16
    label = 24
    sheet = Image.new("RGB", (width * len(PAGES), (height + label) * len(args.engines)), "#152029")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    references = {page: Image.open(args.directory / f"chromium-{page}.png").convert("RGB") for page in PAGES}
    for row, engine in enumerate(args.engines):
        result = {}
        for col, page in enumerate(PAGES):
            x, y = col * width, row * (height + label)
            draw.text((x + 8, y + 5), f"{engine} / {page}", fill="white", font=font)
            path = args.directory / f"{engine}-{page}.png"
            if not path.exists():
                continue
            with Image.open(path) as source:
                image = source.convert("RGB")
            if image.size != references[page].size:
                raise ValueError(f"{path}: {image.size}")
            result[page], delta = metrics(references[page], image)
            if engine != "chromium":
                delta.point(lambda value: min(255, 3 * value)).save(args.directory / f"diff-{engine}-{page}.png")
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            sheet.paste(image, (x, y + label))
        print(json.dumps({"engine": engine, "diff": result}))
    sheet.save(args.sheet)


if __name__ == "__main__":
    main()
