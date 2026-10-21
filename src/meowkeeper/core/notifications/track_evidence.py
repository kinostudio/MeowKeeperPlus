"""以既有追蹤裁切影像建立編號對照，不另開攝影機或執行模型。"""

from __future__ import annotations

from io import BytesIO

from meowkeeper.core.tracking.base import TrackObservation


def numbered_track_sheet(observations: list[TrackObservation]) -> bytes | None:
    """保留摘要順序；沒有影像的軌跡使用空白格，不冒用其他軌跡照片。"""
    from PIL import Image, ImageDraw, ImageOps

    selected = observations[:20]
    if not selected or not any(item.crop_jpeg for item in selected):
        return None
    columns = min(4, len(selected))
    rows = (len(selected) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 256, rows * 216), "#eee7dc")
    draw = ImageDraw.Draw(sheet)
    valid_count = 0
    for index, observation in enumerate(selected):
        x, y = (index % columns) * 256, (index // columns) * 216
        draw.text((x + 8, y + 5), f"Track {index + 1}", fill="#302d27")
        if not observation.crop_jpeg:
            continue
        try:
            with Image.open(BytesIO(observation.crop_jpeg)) as image:
                thumb = ImageOps.contain(image.convert("RGB"), (240, 184))
                sheet.paste(thumb, (x + (256 - thumb.width) // 2, y + 26))
                valid_count += 1
        except (OSError, ValueError):
            continue
    if not valid_count:
        return None
    output = BytesIO()
    sheet.save(output, format="JPEG", quality=85)
    return output.getvalue()
