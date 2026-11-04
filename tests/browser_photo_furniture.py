"""真实照片家具端到端浏览器验收。独立无登录Chromium，产物留在 --demo-dir。"""
import argparse
import base64
import copy
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import math
from pathlib import Path
import threading
import traceback

import numpy as np
from PIL import Image
import trimesh
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("photo_plan_tools", ROOT / "skills/floorplan-image/scripts/plan_tools.py")
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)

CAMERA_API = """
// 仅用于检视捕获副本：只改变相机姿态，不改几何、材质、灯光。
window.PhotoFurnitureCapture = {
  focus(id){
    const object=furnG.children.find(g=>g.userData.fid===id);
    if(!object)throw new Error('Furniture not loaded');
    const bounds=new THREE.Box3().setFromObject(object,true);
    const center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
    const distance=Math.max(size.x,size.y,size.z)/(2*Math.tan(camera.fov*Math.PI/360))*1.4;
    const direction=new THREE.Vector3(.85,.5,1.3).normalize();
    fly=null;orbit.target.copy(center);camera.position.copy(center).addScaledVector(direction,distance);
    camera.lookAt(center);camera.updateProjectionMatrix();orbit.update();
    return {center:center.toArray(),position:camera.position.toArray(),bounds:[bounds.min.toArray(),bounds.max.toArray()]};
  }
};
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def capture_copy(source, target):
    text = source.read_text()
    position = text.rfind("</script>")
    assert position >= 0 and "let renderer" in text and "let archFloor" in text
    target.write_text(text[:position] + CAMERA_API + text[position:])


def normalized_mesh_vertices(asset):
    payload = base64.b64decode(asset["dataUrl"].split(",", 1)[1])
    mesh = trimesh.load(io.BytesIO(payload), file_type="glb", force="mesh", process=False)
    assert len(mesh.vertices) > 0 and len(mesh.faces) > 0
    return np.asarray(mesh.vertices, dtype=np.float64), hashlib.sha256(payload).hexdigest()


def expected_vertex_bounds(vertices, furniture, default_height):
    """逐点缩放、旋转、平移真实网格，不能用旋转包围矩形替代。"""
    points = vertices * np.array([furniture["w"], furniture.get("height", default_height), furniture["d"]])
    angle = math.radians(furniture["rot"])
    c, s = math.cos(angle), math.sin(angle)
    x, z = points[:, 0].copy(), points[:, 2].copy()
    points[:, 0] = c*x - s*z + furniture["cx"]
    points[:, 2] = s*x + c*z + furniture["cy"]
    lo, hi = points.min(axis=0), points.max(axis=0)
    return {"min": [float(lo[0]), float(lo[2])], "max": [float(hi[0]), float(hi[2])],
            "bottomMm": float(lo[1]), "heightMm": float(hi[1]-lo[1])}


def run(args):
    demo = args.demo_dir.resolve()
    plan = json.loads((demo / "plan.json").read_text())
    target = next(f for f in plan["furniture"] if f.get("assetId") and (not args.item_id or f["id"] == args.item_id))
    fid, asset_id = target["id"], target["assetId"]
    asset = plan["furnitureAssets"][asset_id]
    vertices, mesh_hash = normalized_mesh_vertices(asset)
    report = {"ok": False, "targetItemId": fid, "assetId": asset_id, "modelSha256": mesh_hash,
              "checks": [], "boundsComparisons": [], "pageErrors": [], "screenshots": [],
              "capturePolicy": "相机API只注入single-chair-capture.html；模型、材质、光照与标准模板相同。",
              "sourceImageLicense": asset.get("provenance", {}).get("inputImageLicense", "unknown")}
    if args.rebuild:
        TOOLS.build(plan, demo / "index.html")
    single = copy.deepcopy(plan)
    single["id"] = plan["id"] + "-single-chair-inspection"
    single["title"] = "照片家具模型检视台 · 非真实房间"
    height = target.get("height", asset["defaultHeightMm"])
    dimensions = {"w": target["w"], "d": target["d"], "height": height}
    size_label = "×".join(f"{dimensions[key]:g}" for key in ("w", "d", "height"))
    stage_half = max(1500, max(target["w"], target["d"]) * 2)
    single["rooms"] = [{"id": "MODEL_STAGE", "name": "模型检视台（非真实房间）", "poly": [[-stage_half,-stage_half],[stage_half,-stage_half],[stage_half,stage_half],[-stage_half,stage_half]], "mat": "tile800", "at": [0,0]}]
    single["walls"], single["windows"], single["doors"], single["slides"] = [], [], [], []
    solo = copy.deepcopy(target)
    solo.update(cx=0, cy=0, rot=0, height=height)
    single["furniture"] = [solo]
    single["furnitureAssets"] = {asset_id: asset}
    single["scale"] = {"status": "estimated", "evidence": f"模型检视台沿用目标家具{size_label}mm声明尺寸；不从照片测量，地台不是房间"}
    single["uncertainties"] = ["单家具外观检视台，不是真实户型。", f"{size_label}mm沿用目标家具声明尺寸；单图不可见面为模型推测。"]
    report["inspectionDimensionsMm"] = dimensions
    (demo / "single-chair.plan.json").write_text(json.dumps(single, ensure_ascii=False, indent=2))
    TOOLS.build(single, demo / "single-chair.html")
    text = (demo / "single-chair.html").read_text()
    notice = f'<div style="position:fixed;left:50%;top:152px;transform:translateX(-50%);z-index:80;background:#fffc;padding:6px 14px;border-radius:8px;font:13px sans-serif;pointer-events:none">单家具模型检视台 · 非真实房间 · {size_label} mm（目标尺寸）</div>'
    (demo / "single-chair.html").write_text(text.replace("</body>", notice + "</body>"))
    capture_copy(demo / "single-chair.html", demo / "single-chair-capture.html")
    handler = functools.partial(QuietHandler, directory=str(demo))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    def current(page):
        return page.evaluate("id=>Floorplan.exportPlan().furniture.find(f=>f.id===id)", fid)

    def ready(page):
        page.wait_for_function("id=>window.View3D?.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='ready'", arg=fid, timeout=60000)
        page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
        page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")

    def enter_3d(page):
        if not page.locator("body").evaluate("e=>e.classList.contains('m3d')"):
            page.locator('[data-view="3d"]').click()
        ready(page)

    def enter_2d(page):
        if page.locator("body").evaluate("e=>e.classList.contains('m3d')"):
            page.locator('[data-view="2d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)

    def compare_bounds(page, label):
        ready(page)
        f = current(page)
        expected = expected_vertex_bounds(vertices, f, asset["defaultHeightMm"])
        actual = page.evaluate("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)", fid)
        errors = [abs(expected[k][i]-actual[k][i]) for k in ["min","max"] for i in [0,1]]
        errors += [abs(expected[k]-actual[k]) for k in ["heightMm","bottomMm"]]
        maximum = max(errors)
        report["boundsComparisons"].append({"label":label,"furniture":f,"expectedFromMeshVertices":expected,"actualWebGLGeometry":actual,"maxErrorMm":maximum})
        assert maximum < .15, (label, maximum, actual, expected)

    def open_file_menu(page):
        if not page.locator("#exportJson").is_visible():
            page.locator("details.menu > summary").click()

    def import_json_ui(page, path):
        open_file_menu(page)
        with page.expect_file_chooser() as chooser:
            page.locator("#importJson").click()
        chooser.value.set_files(str(path))
        page.wait_for_function("document.querySelector('#toast').textContent.includes('方案已导入')")

    def initialize(page, filename):
        page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
        page.goto(base_url + "/" + filename)
        page.wait_for_function("!!window.Floorplan && !!window.View3D", timeout=90000)

    try:
        with sync_playwright() as pw:
            options = {"headless":True,"args":["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-webgl","--ignore-certificate-errors"]}
            if args.chromium:
                options["executable_path"] = str(args.chromium)
            browser = pw.chromium.launch(**options)
            report["browserVersion"] = browser.version
            context = browser.new_context(viewport={"width":1600,"height":1100}, ignore_https_errors=True, accept_downloads=True)
            if not args.capture_only:
                page = context.new_page()
                initialize(page, "index.html")
                image = page.locator(f'svg g.furn[data-fid="{fid}"] image[data-asset-id="{asset_id}"]')
                assert image.count() == 1
                assert image.get_attribute("href") == asset["topViewDataUrl"]
                png = Image.open(io.BytesIO(base64.b64decode(asset["topViewDataUrl"].split(",",1)[1])))
                png.load()
                assert png.size[0] > 0 and png.mode == "RGBA"
                page.screenshot(path=str(demo / "actual-2d.png"))
                report["screenshots"].append("actual-2d.png")
                report["checks"].append("2D使用本次真实网格俯视PNG，完整解码且与嵌入资产匹配")
                image.click()
                assert page.locator("#fH").input_value() == str(int(target.get("height",asset["defaultHeightMm"])))
                enter_3d(page)
                compare_bounds(page, "initial")
                if page.locator("#back").is_visible():
                    page.locator("#back").click()
                page.locator('[data-cut="1.2"]').click()
                ready(page)
                page.screenshot(path=str(demo / "actual-3d.png"))
                report["screenshots"].append("actual-3d.png")
                report["checks"].append("实际Three.js加载照片重建GLB，落地和初始尺寸逐顶点一致")
                enter_2d(page)
                if "on" in (page.locator('[data-layer="wallSnap"]').get_attribute("class") or ""):
                    page.locator('[data-layer="wallSnap"]').click()
                before_drag = current(page)
                box = image.bounding_box()
                assert box and box["width"]>5 and box["height"]>5
                x,y = box["x"]+box["width"]*.5,box["y"]+box["height"]*.7
                page.mouse.move(x,y);page.mouse.down()
                page.mouse.move(x+44,y+36,steps=12);page.mouse.up()
                after_drag = current(page)
                assert math.hypot(after_drag["cx"]-before_drag["cx"],after_drag["cy"]-before_drag["cy"])>50
                assert after_drag["assetId"] == asset_id
                report["drag"] = {"before":before_drag,"after":after_drag,"mouseDeltaPx":[44,36],"usedActualMouse":True}
                report["checks"].append("真实鼠标拖动自定义PNG家具，位置改变并保留3D资产")
                states = []
                for selector,value in [("#fW","530"),("#fD","590"),("#fH","970"),("#fR","37"),("#fX","2230"),("#fY","870")]:
                    states.append(current(page))
                    page.locator(selector).fill(value);page.locator(selector).press("Tab")
                edited = current(page)
                enter_3d(page)
                compare_bounds(page,"edited_rot37_scaled_moved")
                page.locator("#undo").click()
                assert current(page) == states[-1]
                compare_bounds(page,"undo")
                page.locator("#redo").click()
                assert current(page) == edited
                compare_bounds(page,"redo")
                report["checks"].append("UI修改宽深高、旋转37度和中心后真实顶点bbox正确；撤销重做恢复几何")
                open_file_menu(page)
                with page.expect_download() as download:
                    page.locator("#exportJson").click()
                exported_path = demo / "edited-export.json"
                download.value.save_as(str(exported_path))
                saved = json.loads(exported_path.read_text())
                assert saved["plan"]["furnitureAssets"][asset_id]["dataUrl"] == asset["dataUrl"]
                assert saved["plan"]["furnitureAssets"][asset_id]["topViewDataUrl"] == asset["topViewDataUrl"]
                assert next(f for f in saved["plan"]["furniture"] if f["id"]==fid) == edited
                open_file_menu(page)
                page.once("dialog",lambda dialog:dialog.accept())
                page.locator("#reset").click()
                assert current(page) == target
                import_json_ui(page,exported_path)
                assert current(page) == edited
                compare_bounds(page,"same_context_json_reimport")
                page.reload()
                page.wait_for_function("!!window.Floorplan && !!window.View3D",timeout=90000)
                assert current(page) == edited
                enter_3d(page);compare_bounds(page,"refresh_persistence")
                report["checks"].append("实际文件菜单导出完整JSON，重置后导入及刷新保留资产和编辑参数")
                fresh = browser.new_context(viewport={"width":1600,"height":1100},ignore_https_errors=True,accept_downloads=True)
                fresh_page = fresh.new_page();initialize(fresh_page,"index.html")
                assert current(fresh_page) == target
                import_json_ui(fresh_page,exported_path)
                assert current(fresh_page) == edited
                enter_3d(fresh_page);compare_bounds(fresh_page,"new_context_json_import")
                report["checks"].append("全新无登录context导入完整JSON，不依赖旧localStorage恢复照片模型")
                fresh.close()
            inspection = browser.new_context(viewport={"width":1500,"height":1100},ignore_https_errors=True)
            inspection_page = inspection.new_page();initialize(inspection_page,"single-chair-capture.html")
            if "on" in (inspection_page.locator('[data-layer="labels"]').get_attribute("class") or ""):
                inspection_page.locator('[data-layer="labels"]').click()
            chair_image = inspection_page.locator(f'svg g.furn[data-fid="{fid}"] image')
            initial_box = chair_image.bounding_box()
            zooms=max(0,min(12,math.ceil(math.log(350/initial_box["width"],1.25))))
            for _ in range(zooms):
                inspection_page.locator("#zoomIn").click()
            b=chair_image.bounding_box();stage=inspection_page.locator("#stage").bounding_box()
            x0,y0=max(stage["x"],b["x"]-24),max(stage["y"],b["y"]-24)
            x1=min(stage["x"]+stage["width"],b["x"]+b["width"]+24)
            y1=min(stage["y"]+stage["height"],b["y"]+b["height"]+24)
            inspection_page.screenshot(path=str(demo/"chair-2d-detail.png"),clip={"x":x0,"y":y0,"width":x1-x0,"height":y1-y0})
            enter_3d(inspection_page)
            report["singleChairCamera"]=inspection_page.evaluate("id=>PhotoFurnitureCapture.focus(id)",fid)
            inspection_page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
            inspection_page.locator("#stage").screenshot(path=str(demo/"chair-3d-detail.png"))
            assert all(current(inspection_page)[key] == value for key, value in dimensions.items())
            report["screenshots"] += ["chair-2d-detail.png","chair-3d-detail.png"]
            report["checks"].append(f"单模型检视台使用同一GLB与标准材质光照，仅相机近拍{size_label}mm目标模型，未改比例")
            inspection.close();context.close();browser.close()
            assert not report["pageErrors"],report["pageErrors"]
        report["ok"]=True
    except Exception as error:
        report["error"]=str(error);report["traceback"]=traceback.format_exc()
        raise
    finally:
        server.shutdown()
        report_name = "browser-photo-capture-report.json" if args.capture_only else "browser-photo-report.json"
        (demo/report_name).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in ["boundsComparisons","traceback"]},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir",type=Path,required=True)
    parser.add_argument("--item-id")
    parser.add_argument("--chromium",type=Path)
    parser.add_argument("--capture-only", action="store_true", help="仅重建检视台及近拍，不重复已通过的行为检查")
    parser.add_argument("--rebuild",action="store_true",help="使用当前Skill模板重建demo/index.html")
    run(parser.parse_args())
