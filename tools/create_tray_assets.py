"""Package approved transparent watercolor PNGs into multi-frame Tray ICOs.

--tray-only does not touch Web assets or require the SVG renderer.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
TARGET = ROOT / "assets" / "tray"
WEB = ROOT / "src" / "meowkeeper" / "app" / "web" / "static" / "icons"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
STATES = ("ready", "waiting", "error")


def with_badge(source: Image.Image, state: str) -> Image.Image:
    """Composite only the corner symbol; never repaint or rescale the cats."""
    colors = {"ready": "#356b35", "waiting": "#bd7a16", "error": "#aa3029"}
    size = source.width
    radius = round(size * .135)
    cx = cy = size - radius - round(size * .04)
    layer = Image.new("RGBA", source.size)
    draw = ImageDraw.Draw(layer)
    width = max(2, round(size * .015))
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=colors[state], outline="#fffaf0", width=width)
    if state == "ready":
        draw.line([(cx-radius*.5,cy),(cx-radius*.1,cy+radius*.35),(cx+radius*.5,cy-radius*.4)], fill="white", width=width*2, joint="curve")
    elif state == "waiting":
        draw.line([(cx,cy-radius*.55),(cx,cy),(cx+radius*.42,cy+radius*.25)], fill="white", width=width*2, joint="curve")
    else:
        draw.line([(cx,cy-radius*.52),(cx,cy+radius*.1)],fill="white",width=width*2)
        r=width
        draw.ellipse((cx-r,cy+radius*.4-r,cx+r,cy+radius*.4+r),fill="white")
    return Image.alpha_composite(source, layer)


def tray_frames(name: str) -> list[Image.Image]:
    with Image.open(BRAND / name) as image:
        if image.mode != "RGBA" or image.getchannel("A").getextrema() != (0, 255):
            raise ValueError("Tray 來源圖必須有真正透明背景與不透明前景。")
        if image.width != image.height or image.width < max(SIZES):
            raise ValueError("Tray 來源圖必須為至少 256 像素的正方形。")
        return [image.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES]


def save_ico(frames: list[Image.Image], path: Path) -> None:
    frames = sorted(frames, key=lambda frame: frame.width, reverse=True)
    frames[0].save(path, format="ICO", sizes=[(frame.width, frame.height) for frame in frames],
                   append_images=frames[1:])


def main() -> int:
    parser = argparse.ArgumentParser(description="將已確認的雙貓狀態圖封裝成 Tray 圖示。")
    parser.add_argument("--tray-only", action="store_true", help="只更新 Tray／啟動圖示，不改 Web 圖示。")
    args = parser.parse_args()
    TARGET.mkdir(parents=True, exist_ok=True)
    save_ico(tray_frames("meowkeeper-cats-watercolor-v1.png"), TARGET / "meowkeeper.ico")
    for state in STATES:
        with Image.open(BRAND / "meowkeeper-cats-watercolor-v1.png") as source:
            with_badge(source, state).save(BRAND / f"meowkeeper-cats-{state}.png", optimize=True)
        frames = tray_frames(f"meowkeeper-cats-{state}.png")
        for name in (f"meowkeeper-earth-{state}.ico", f"meowkeeper-{state}.ico"):
            save_ico(frames, TARGET / name)
    if args.tray_only:
        return 0
    WEB.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BRAND / "meowkeeper-cats-watercolor-v1.png", WEB / "meowkeeper-logo.png")
    for size in (32, 192, 512):
        with Image.open(BRAND / "meowkeeper-cats-watercolor-v1.png") as source:
            source.resize((size,size),Image.Resampling.LANCZOS).save(WEB / f"meowkeeper-{size}.png", optimize=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
