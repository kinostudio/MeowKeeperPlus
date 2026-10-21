"""Create deterministic multi-size ICO assets for the Windows Tray."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "tray"
SIZES = tuple((size, size) for size in (16, 20, 24, 32, 48, 64, 128, 256))
COLORS = {
    # Warm earth-tone shell; the light accent keeps each state legible at 16 px.
    "ready": ((73, 55, 40), (206, 214, 164)),
    "waiting": ((73, 55, 40), (235, 188, 117)),
    "error": ((73, 55, 40), (226, 139, 117)),
}


def make_icon(background: tuple[int, int, int], accent: tuple[int, int, int], size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (*background, 255))
    draw = ImageDraw.Draw(image)
    margin = max(1, size // 8)
    draw.ellipse((margin, margin, size - margin, size - margin), fill=(*accent, 255))
    # Cut a paw from the accent disc; toe marks remain separate at 16 px.
    draw.ellipse((size * .29, size * .55, size * .71, size * .80), fill=(*background, 255))
    for x, y in ((.32, .39), (.50, .30), (.68, .39)):
        draw.ellipse(
            ((x - .065) * size, (y - .065) * size, (x + .065) * size, (y + .065) * size),
            fill=(*background, 255),
        )
    return image


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    for state, (background, accent) in COLORS.items():
        base = make_icon(background, accent, 256)
        for filename in (f"meowkeeper-earth-{state}.ico", f"meowkeeper-{state}.ico"):
            base.save(TARGET / filename, format="ICO", sizes=SIZES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
