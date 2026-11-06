"""把计划矢量覆盖在原图上；标准库，无额外依赖。"""
import argparse
import base64
import html
import json
import mimetypes
from pathlib import Path
from plan_tools import validate


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("image");a.add_argument("plan");a.add_argument("--output",required=True)
    a.add_argument("--mm-per-pixel",type=float,required=True)
    a.add_argument("--origin-x",type=float,default=0);a.add_argument("--origin-y",type=float,default=0)
    a.add_argument("--width",type=int);a.add_argument("--height",type=int)
    a.add_argument("--floor-id",help="多层建筑中要叠加的楼层；坐标仍使用该层独立标定")
    v=a.parse_args()
    if v.mm_per_pixel<=0:a.error("mm-per-pixel 必须大于0")
    p=json.loads(Path(v.plan).read_text());validate(p)
    if p.get('schemaVersion') == 2:
        floors=[f for f in p['floors'] if f['id']==v.floor_id]
        if len(floors)!=1:a.error('多层叠图需要有效 --floor-id，不把全部楼层叠在同一张图上')
        p=floors[0]['plan']
    data=Path(v.image).read_bytes()
    w,h=v.width,v.height
    if not w or not h:
        # PNG/JPEG/WebP 等由用户给出尺寸；PNG可直接解析头部。
        if data[:8]==b"\x89PNG\r\n\x1a\n":
            w=int.from_bytes(data[16:20],"big");h=int.from_bytes(data[20:24],"big")
        else:a.error("非 PNG 图片需要 --width 和 --height")
    mime=mimetypes.guess_type(v.image)[0]
    if mime not in {"image/png","image/jpeg","image/webp"}:a.error("仅支持 PNG/JPEG/WebP 原图")
    def xy(x,y):return [x/v.mm_per_pixel+v.origin_x,y/v.mm_per_pixel+v.origin_y]
    def points(poly):return " ".join("%g,%g"%tuple(xy(*q)) for q in poly)
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',f'<image width="{w}" height="{h}" href="data:{mime};base64,{base64.b64encode(data).decode()}" opacity="0.65"/>']
    for r in p["rooms"]:
        out.append(f'<polygon points="{points(r["poly"])}" fill="#00ccaa" fill-opacity=".08" stroke="#00aa88" stroke-width="2"/>')
        for hole in r.get('holes',[]):out.append(f'<polygon points="{points(hole)}" fill="none" stroke="#ff9900" stroke-dasharray="5 3" stroke-width="2"/>')
    for wall in p["walls"]:
        x0,y0,x1,y1=wall[:4];poly=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
        out.append(f'<polygon points="{points(poly)}" fill="#ff3300" fill-opacity=".2" stroke="#ff3300"/>')
    for rect in p["windows"]+[d["rect"] for d in p["doors"]+p["slides"]]:
        x0,y0,x1,y1=rect;out.append(f'<polygon points="{points([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])}" fill="none" stroke="#00aaff" stroke-width="2"/>')
    for f in p["furniture"]:
        x,y=xy(f["cx"],f["cy"]);ww,dd=f["w"]/v.mm_per_pixel,f["d"]/v.mm_per_pixel
        out.append(f'<g transform="translate({x} {y}) rotate({f["rot"]})"><rect x="{-ww/2}" y="{-dd/2}" width="{ww}" height="{dd}" fill="none" stroke="#cc00ff" stroke-width="2"/><path d="M{-ww/2},0 H{ww/2} M0,{-dd/2} V{dd/2}" stroke="#cc00ff"/></g>')
        out.append(f'<text x="{x}" y="{y}" font-size="12" fill="#8800aa">{html.escape(f["id"])}</text>')
    out.append('</svg>');target=Path(v.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text("\n".join(out))
    print(target)

if __name__=="__main__":main()
