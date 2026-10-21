"""人工圈選單隻貓的本機參考照片；不呼叫模型或雲端。"""
import math
from io import BytesIO


def crop_reference(content, polygon, *, minimum=96):
    from PIL import Image, ImageDraw, ImageOps
    if (not isinstance(polygon,list) or not 3 <= len(polygon) <= 64
            or any(not isinstance(point,list) or len(point)!=2 or any(type(value) not in {int,float}
                or not math.isfinite(value) or not 0<=value<=1 for value in point) for point in polygon)):
        raise ValueError("請在圖片內框選有效範圍。")
    if not isinstance(content,bytes) or not 1<=len(content)<=8*1024*1024:
        raise ValueError("照片不可超過8MB。")
    with Image.open(BytesIO(content)) as opened:
        if opened.format not in {"JPEG","PNG","WEBP"} or opened.width*opened.height>16_000_000:
            raise ValueError("照片格式或尺寸不合格。")
        image=ImageOps.exif_transpose(opened).convert("RGB")
    points=[(round(x*image.width),round(y*image.height)) for x,y in polygon]
    left,top,right,bottom=min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)
    if min(right-left,bottom-top)<minimum:
        raise ValueError(f"選取範圍太小；寬高至少{minimum}像素，請選較清楚的照片，不會放大冒充細節。")
    mask=Image.new("L",image.size,0);ImageDraw.Draw(mask).polygon(points,fill=255)
    cropped=Image.composite(image,Image.new("RGB",image.size,(128,128,128)),mask).crop((left,top,right,bottom))
    cropped.thumbnail((1280,1280));target=BytesIO();cropped.save(target,format="JPEG",quality=92)
    return target.getvalue()
