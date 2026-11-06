"""户型数据校验和单文件 HTML 构建，Python 3.8+，无第三方依赖。"""
import argparse
import base64
import mimetypes
import sys
import copy
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'scripts'))
from reconstruct import refine_plan
from asset_validation import validate_asset

CATALOG = json.loads((ROOT / "references/furniture-catalog.json").read_text())
TYPES = {x["type"] for x in CATALOG}
MATERIALS = {"wood", "walnut", "tile800", "tile600", "marble", "antislip", "terrazzo", "carpet"}


def finite(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and abs(n) <= 10000000


def point(p):
    return isinstance(p, list) and len(p) == 2 and all(finite(n) for n in p)


def rect(r):
    return isinstance(r, list) and len(r) >= 4 and all(finite(n) for n in r[:4]) and r[2] > r[0] and r[3] > r[1]


def token(s):
    return isinstance(s, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", s) is not None


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def segments_touch(a, b, c, d):
    def on(a, b, p):
        return abs(cross(a,b,p)) < 1e-6 and min(a[0],b[0])-1e-6 <= p[0] <= max(a[0],b[0])+1e-6 and min(a[1],b[1])-1e-6 <= p[1] <= max(a[1],b[1])+1e-6
    if cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0:
        return True
    return any([on(a,b,c), on(a,b,d), on(c,d,a), on(c,d,b)])


def inside(p, poly):
    hit = False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        if abs(cross(a,b,p)) < 1e-6 and min(a[0],b[0])-1e-6 <= p[0] <= max(a[0],b[0])+1e-6 and min(a[1],b[1])-1e-6 <= p[1] <= max(a[1],b[1])+1e-6:
            return True
        if (a[1]>p[1]) != (b[1]>p[1]) and p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:
            hit = not hit
    return hit


def overlap(a,b):
    return min(a[2],b[2])-max(a[0],b[0]) > 1 and min(a[3],b[3])-max(a[1],b[1]) > 1


def validate(plan):
    """返回告警；非法数据抛 ValueError，绝不静默重画/补家具。"""
    def require(ok,msg):
        if not ok:
            raise ValueError(msg)
    p=plan
    require(isinstance(p,dict),"计划必须是 JSON 对象")
    if p.get('schemaVersion') == 2:
        from building import validate_building
        return validate_building(p)
    require(p.get("schemaVersion")==1 and p.get("units")=="mm" and token(p.get("id")),"需要 schemaVersion:1、units:mm 和有效 id")
    require(isinstance(p.get("title"),str),"缺少 title")
    require(finite(p.get("height")) and 2000 <= p["height"] <= 6000,"层高需要 2000～6000 mm")
    require(isinstance(p.get("scale"),dict) and p["scale"].get("status") in {"measured","estimated","relative"},"需要尺度状态")
    if p["scale"]["status"]=="measured":
        require(bool(p["scale"].get("evidence")),"measured 必须给出已知尺寸依据")
    for k in ("rooms","walls","windows","doors","slides","furniture"):
        require(isinstance(p.get(k),list),k+" 必须为数组")
    require(bool(p["rooms"]),"至少需要一个房间")
    warnings=[]
    if p["scale"]["status"]!="measured":
        warnings.append("尺寸为估计/相对尺度，面积与造价不代表实测")
    if p.get('reference'):
        r=p['reference']
        require(isinstance(r,dict) and point(r.get('originPx')) and all(finite(r.get(k)) and r[k]>0 for k in ('width','height','mmPerPixel')),'参考图标定无效')
        if r.get('dataUrl'):require(bool(re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+',r['dataUrl'])),'参考图编码无效')
    ids=set()
    for r in p["rooms"]:
        require(isinstance(r,dict) and token(r.get("id")) and r["id"] not in ids,"房间 id 无效/重复")
        ids.add(r["id"])
        require(isinstance(r.get("name"),str) and r.get("mat") in MATERIALS,"房间名称/材质无效")
        require("floorRotation" not in r or finite(r["floorRotation"]),"地板角度无效")
        require("floorColor" not in r or bool(re.fullmatch(r"#[0-9a-fA-F]{6}",r["floorColor"])),"地板颜色无效")
        poly=r.get("poly")
        require(isinstance(poly,list) and len(poly)>=3 and all(point(v) for v in poly),r["id"]+" 轮廓无效")
        require(len({tuple(v) for v in poly})==len(poly),r["id"]+" 重复顶点（首尾无需重复）")
        a=sum(u[0]*v[1]-v[0]*u[1] for u,v in zip(poly,poly[1:]+poly[:1]))/2
        require(abs(a)>1,r["id"]+" 面积为零")
        n=len(poly)
        for i in range(n):
            for j in range(i+1,n):
                if j==i+1 or (i==0 and j==n-1):
                    continue
                require(not segments_touch(poly[i],poly[(i+1)%n],poly[j],poly[(j+1)%n]),r["id"]+" 轮廓自交")
        if r.get("at") is not None:
            require(point(r["at"]) and inside(r["at"],poly),r["id"]+" 标签点必须在房间内")
        from building import validate_holes
        validate_holes(r)
    for w in p["walls"]:
        require(rect(w) and len(w)==5 and w[4] in {"b","e","n","low","u"},"墙体需为 [x0,y0,x1,y1,b/e/n/low/u]")
    for w in p["windows"]:
        require(rect(w) and len(w)==4,"窗矩形无效")
    openings=list(p["windows"])
    for d in p["doors"]:
        require(isinstance(d,dict) and rect(d.get("rect")) and all(point(d.get(k)) for k in ("h","c","o")) and finite(d.get("len")) and d["len"]>0,"门数据无效")
        c,o=d["c"],d["o"]
        require(abs(math.hypot(*c)-1)<.001 and abs(math.hypot(*o)-1)<.001 and abs(sum(x*y for x,y in zip(c,o)))<.001,"门开启/关闭方向需为正交单位向量")
        require(all(abs(v) in (0,1) for v in c+o),"首版门方向仅支持正交轴向")
        openings.append(d["rect"])
    for d in p["slides"]:
        require(isinstance(d,dict) and rect(d.get("rect")) and isinstance(d.get("v"),bool),"推拉门数据无效")
        openings.append(d["rect"])
    for i,o in enumerate(openings):
        require(not any(overlap(o,w) for w in p["walls"]),"门窗洞口 %d 被实体墙覆盖，需先切开墙段"%i)
    assets=p.get("furnitureAssets",{})
    require(isinstance(assets,dict),"furnitureAssets 必须为资产id到资产的对象")
    for aid,asset in assets.items():
        validate_asset(asset,aid)
    ids=set()
    for f in p["furniture"]:
        require(isinstance(f,dict) and token(f.get("id")) and f["id"] not in ids,"家具 id 无效/重复")
        ids.add(f["id"])
        require(f.get("type") in TYPES,"未知家具类型: "+str(f.get("type")))
        require("height" not in f or (finite(f["height"]) and 1 <= f["height"] <= 10000),"家具高度需为1～10000mm")
        if "assetId" in f:
            require(token(f["assetId"]) and f["assetId"] in assets,"家具引用了不存在的资产")
        require(isinstance(f.get("name"),str) and all(finite(f.get(k)) for k in ("cx","cy","w","d","rot")) and f["w"]>0 and f["d"]>0,"家具位置/尺寸无效")
        require(bool(re.fullmatch(r"#[0-9a-fA-F]{6}",f.get("color","#dddddd"))),"家具颜色需 #RRGGBB")
        if f.get('placement'):
            require(isinstance(f['placement'],dict) and f['placement'].get('mode') in ('wall','free'),'placement.mode 需要 wall/free')
        for k in ("accentColor","frameColor","pillowColor"):
            require(k not in f or bool(re.fullmatch(r"#[0-9a-fA-F]{6}",f[k])),"家具配色无效")
        from building import usable
        require(not any(inside([f['cx'], f['cy']], hole) for r in p['rooms'] for hole in r.get('holes', [])), f['id']+' 家具中心落在楼板洞口中')
        if not any(usable([f["cx"],f["cy"]],r) for r in p["rooms"]):
            warnings.append(f["id"]+" 家具中心位于所有房间之外")
        rad=math.radians(f["rot"]);c,s=math.cos(rad),math.sin(rad)
        corners=[[f["cx"]+c*x-s*y,f["cy"]+s*x+c*y] for x,y in [(-f["w"]/2,-f["d"]/2),(f["w"]/2,-f["d"]/2),(f["w"]/2,f["d"]/2),(-f["w"]/2,f["d"]/2)]]
        if any(not any(inside(v,r["poly"]) for r in p["rooms"]) for v in corners):
            warnings.append(f["id"]+" 家具边角可能越界；检查是否为正常的墙挂或叠放")
    return warnings


def build(plan,output,reference_image=None):
    validate(plan)
    if plan.get('schemaVersion') == 2:
        from building import refine_building
        plan,adjustments=refine_building(plan)
        if reference_image:
            raise ValueError('多层参考图请使用 building.py 清单为各层指定 imageFile')
    else:
        plan,adjustments=refine_plan(plan)
    warnings=validate(plan)
    if reference_image:
        image=Path(reference_image);mime=mimetypes.guess_type(str(image))[0]
        if mime not in {'image/png','image/jpeg','image/webp'}:raise ValueError('参考图仅支持 PNG/JPEG/WebP')
        if not plan.get('reference'):raise ValueError('参考图需要 reference 像素标定信息')
        plan['reference']['dataUrl']='data:'+mime+';base64,'+base64.b64encode(image.read_bytes()).decode('ascii')
    template=(ROOT/"assets/index.html").read_text()
    payload=json.dumps(plan,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c").replace("\u2028","\\u2028").replace("\u2029","\\u2029")
    pattern=r"/\* PLAN_DATA_START \*/.*?/\* PLAN_DATA_END \*/"
    html,n=re.subn(pattern,lambda _:"/* PLAN_DATA_START */ "+payload+" /* PLAN_DATA_END */",template,count=1,flags=re.S)
    if n!=1:
        raise ValueError("模板缺少 PLAN_DATA 槽")
    target=Path(output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(html)
    return warnings


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("command",choices=["validate","build"])
    cli.add_argument("plan")
    cli.add_argument("--output")
    cli.add_argument("--reference-image")
    a=cli.parse_args()
    try:
        p=json.loads(Path(a.plan).read_text())
        if a.command=="build" and not a.output:
            cli.error("build 需要 --output")
        warnings=build(p,a.output,a.reference_image) if a.command=="build" else validate(p)
        print(json.dumps({"ok":True,"warnings":warnings,"output":a.output},ensure_ascii=False))
    except (ValueError,KeyError,TypeError) as e:
        cli.exit(1,"校验失败: "+str(e)+"\n")

if __name__=="__main__":
    main()
