"""Create deterministic Tray/launcher/web icons from the brand SVGs.

Sources: assets/brand/meowkeeper-logo.svg (32 px and up) and
meowkeeper-logo-small.svg (16–24 px: two-tone cat head + red ball, readable
in the notification area). A status badge marks ready / waiting / error.
Requires `rsvg-convert` (librsvg) on the build machine; generated files are
committed, so end users and the Windows build never need it.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
TARGET = ROOT / "assets" / "tray"
WEB = ROOT / "src" / "meowkeeper" / "app" / "web" / "static" / "icons"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
SMALL_LIMIT = 24
INK = (62, 48, 39, 255)
BADGES = {
    "ready": (92, 160, 96, 255),
    "waiting": (231, 165, 52, 255),
    "error": (200, 64, 56, 255),
}


def render(svg: Path, size: int) -> Image.Image:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise RuntimeError("需要 rsvg-convert 產生圖示；產物已提交，終端使用者不需要。")
    png = subprocess.run([converter, "-w", str(size), "-h", str(size), str(svg)],
                         check=True, capture_output=True, timeout=60, shell=False).stdout
    return Image.open(io.BytesIO(png)).convert("RGBA")


def base(size: int) -> Image.Image:
    small = size <= SMALL_LIMIT
    return render(BRAND / ("meowkeeper-logo-small.svg" if small else "meowkeeper-logo.svg"), size)


def with_badge(image: Image.Image, state: str) -> Image.Image:
    size = image.width
    radius = max(3.0, size * (0.2 if size <= SMALL_LIMIT else 0.17))
    centre = size - radius - max(0.5, size * 0.01)
    badged = image.copy()
    draw = ImageDraw.Draw(badged)
    outline = max(1, round(size / 24))
    box = (centre - radius, centre - radius, centre + radius, centre + radius)
    draw.ellipse(box, fill=BADGES[state], outline=(255, 255, 255, 255), width=outline)
    if state == "error" and size >= 32:
        bar = max(2, round(radius * 0.28))
        draw.rectangle((centre - bar / 2, centre - radius * 0.55, centre + bar / 2, centre + radius * 0.15), fill=(255, 255, 255, 255))
        draw.ellipse((centre - bar / 2, centre + radius * 0.3, centre + bar / 2, centre + radius * 0.3 + bar), fill=(255, 255, 255, 255))
    elif state == "waiting" and size >= 32:
        dot = max(1.5, radius * 0.16)
        for offset in (-0.45, 0, 0.45):
            x = centre + offset * radius
            draw.ellipse((x - dot, centre - dot, x + dot, centre + dot), fill=(255, 255, 255, 255))
    return badged


def save_ico(frames: list[Image.Image], path: Path) -> None:
    frames = sorted(frames, key=lambda frame: frame.width, reverse=True)
    frames[0].save(path, format="ICO", sizes=[(frame.width, frame.height) for frame in frames],
                   append_images=frames[1:])


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    plain = {size: base(size) for size in SIZES}
    save_ico(list(plain.values()), TARGET / "meowkeeper.ico")
    for state in BADGES:
        frames = [with_badge(plain[size], state) for size in SIZES]
        for name in (f"meowkeeper-earth-{state}.ico", f"meowkeeper-{state}.ico"):
            save_ico(frames, TARGET / name)
    shutil.copyfile(BRAND / "meowkeeper-logo.svg", WEB / "meowkeeper-logo.svg")
    for size in (32, 192, 512):
        (render(BRAND / "meowkeeper-logo.svg", size)).save(WEB / f"meowkeeper-{size}.png", optimize=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
