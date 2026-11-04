"""自定义GLB家具的真实浏览器验收；仅使用自制小网格，不访问登录资料。"""
import base64
import functools
import http.server
import importlib.util
import io
import json
import math
from pathlib import Path
import struct
import threading

from PIL import Image
from playwright.sync_api import sync_playwright
from browser_runtime import configure_browser_runtime

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plan_tools", ROOT / "skills/floorplan-image/scripts/plan_tools.py")
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


def fixture_asset(asset_id="QABox", normalized=True, external=False, too_many_triangles=False):
    # 自制的前红后蓝渐变方体。PNG为同一已知几何的正交俯视，不是家具识别案例。
    width = .5 if normalized else .25
    positions = [(-width, 0, -.5), (width, 0, -.5), (width, 1, -.5), (-width, 1, -.5),
                 (-width, 0, .5), (width, 0, .5), (width, 1, .5), (-width, 1, .5)]
    indices = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
               3, 7, 6, 3, 6, 2, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5]
    colors = bytes([40, 100, 180, 255] * 4 + [180, 60, 40, 255] * 4)
    verts = struct.pack("<24f", *(v for p in positions for v in p))
    inds = struct.pack("<36H", *indices)
    binary = verts + inds + colors
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}], "buffers": [{"byteLength": len(binary)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(verts), "target": 34962},
                            {"buffer": 0, "byteOffset": len(verts), "byteLength": len(inds), "target": 34963},
                            {"buffer": 0, "byteOffset": len(verts) + len(inds), "byteLength": len(colors), "target": 34962}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [-width, 0, -.5], "max": [width, 1, .5]},
                          {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
                          {"bufferView": 2, "componentType": 5121, "normalized": True, "count": 8, "type": "VEC4"}],
            "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": .9}}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 2}, "indices": 1, "material": 0}]}]}
    if too_many_triangles:
        gltf["accessors"][1]["count"] = 750003
    if external:
        gltf["buffers"][0]["uri"] = "https://invalid.example/not-requested.bin"
    doc = json.dumps(gltf, separators=(",", ":")).encode()
    doc += b" " * (-len(doc) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 28 + len(doc) + len(binary))
    glb += struct.pack("<II", len(doc), 0x4E4F534A) + doc
    glb += struct.pack("<II", len(binary), 0x004E4942) + binary
    image = Image.new("RGBA", (64, 64))
    image.putdata([(round(40 + 140 * y / 63), round(100 - 40 * y / 63), round(180 - 140 * y / 63), 255)
                   for y in range(64) for _ in range(64)])
    png = io.BytesIO()
    image.save(png, format="PNG")
    return {"id": asset_id, "format": "glb", "dataUrl": "data:model/gltf-binary;base64," + base64.b64encode(glb).decode(),
            "topViewDataUrl": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode(),
            "defaultHeightMm": 900, "provenance": {"method": "self-authored-QA-fixture", "license": "CC0", "sourceImageSha256": "0" * 64}}


def run():
    output = ROOT / "outputs/furniture-assets-browser-qa"
    output.mkdir(parents=True, exist_ok=True)
    asset = fixture_asset()
    (output / "fixture.asset.json").write_text(json.dumps(asset))
    plan = json.loads((ROOT / "examples/two-room.json").read_text())
    baseline = json.loads(json.dumps(plan))
    plan["id"] = "asset-browser-qa"
    plan["furnitureAssets"] = {asset["id"]: asset}
    target = plan["furniture"][0]
    target.update(assetId=asset["id"], height=900)
    fid = target["id"]
    TOOLS.build(plan, output / "index.html")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--ignore-certificate-errors"])
            context = browser.new_context(viewport={"width": 1500, "height": 1000}, ignore_https_errors=True, accept_downloads=True)
            configure_browser_runtime(context)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}/outputs/furniture-assets-browser-qa/index.html")
            page.wait_for_function("!!window.Floorplan")
            page.wait_for_function("!!window.View3D", timeout=90000)
            assert page.locator(f'[data-fid="{fid}"] image[data-asset-id="QABox"]').count() == 1
            page.locator(f'[data-fid="{fid}"]').click()
            assert page.locator("#fH").input_value() == "900"
            page.screenshot(path=str(output / "custom-2d.png"))
            checks.append("内嵌资产俯视PNG与高度属性显示")
            page.locator('[data-view="3d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            def ready():
                page.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='ready'", arg=fid)
            def bounds():
                return page.evaluate("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)", fid)
            ready()
            assert abs(bounds()["heightMm"] - 900) < .1
            assert abs(bounds()["bottomMm"]) < .1
            checks.append("真实GLB加载、落地及高度一致")
            for selector, value in [("#fW", "1730"), ("#fD", "1230"), ("#fH", "1110"), ("#fR", "37"), ("#fX", "1910"), ("#fY", "1620")]:
                page.locator(selector).fill(value)
                page.locator(selector).press("Tab")
            ready()
            current = page.evaluate("id=>Floorplan.exportPlan().furniture.find(f=>f.id===id)", fid)
            b = bounds()
            c, s = abs(math.cos(math.radians(current["rot"]))), abs(math.sin(math.radians(current["rot"])))
            half_w, half_d = (current["w"] * c + current["d"] * s) / 2, (current["w"] * s + current["d"] * c) / 2
            assert abs(b["min"][0] - (current["cx"] - half_w)) < .1, b
            assert abs(b["max"][1] - (current["cy"] + half_d)) < .1, b
            assert abs(b["heightMm"] - 1110) < .1
            page.locator("#undo").click()
            page.locator("#redo").click()
            ready()
            checks.append("宽深高、旋转位置、撤销重做后3D占地一致")
            page.screenshot(path=str(output / "custom-3d.png"))
            # 反复重建后，缓存模型仍有可见网格，不能被旧实例dispose破坏。
            for width in [1760, 1800, 1730]:
                page.locator("#fW").fill(str(width)); page.locator("#fW").press("Tab"); ready()
            checks.append("连续重建不破坏缓存模型")
            page.locator('details.menu > summary').click()
            with page.expect_download() as download:
                page.locator("#exportJson").click()
            download.value.save_as(str(output / "export.json"))
            exported = json.loads((output / "export.json").read_text())
            assert exported["plan"]["furnitureAssets"]["QABox"]["dataUrl"] == asset["dataUrl"]
            page.evaluate("p=>Floorplan.loadPlan(p)", baseline)
            page.locator("#fileIn").set_input_files(str(output / "export.json"))
            page.wait_for_function("Floorplan.exportPlan().id==='asset-browser-qa'")
            ready()
            page.reload(); page.wait_for_function("!!window.Floorplan && !!window.View3D")
            assert page.evaluate("Floorplan.exportPlan().furnitureAssets.QABox.dataUrl") == asset["dataUrl"]
            page.locator(f'[data-fid="{fid}"]').click()
            page.locator('[data-view="3d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            ready()
            checks.append("完整JSON真实导出导入与刷新恢复资产")
            page.locator("#fAssetRestore").click()
            assert page.evaluate("id=>!Floorplan.exportPlan().furniture.find(f=>f.id===id).assetId", fid)
            page.locator("#undo").click(); ready()
            checks.append("恢复默认造型后可撤销回图片模型")
            # 实际UI文件输入再次替换，并保留当前平面位置。
            page.locator("#fAssetFile").set_input_files(str(output / "fixture.asset.json"))
            page.wait_for_function("id=>Floorplan.exportPlan().furniture.find(f=>f.id===id)?.height===1110", arg=fid)
            page.wait_for_function("document.querySelector('#toast').textContent.includes('家具资产已替换')")
            ready()
            checks.append("资产JSON通过实际文件输入替换家具并保留已有高度")
            before = page.evaluate("JSON.stringify(Floorplan.exportPlan())")
            bad = fixture_asset("BadExternal", external=True)
            error = page.evaluate("async v=>{try{await Floorplan.attachFurnitureAsset(v.id,v.asset);return null}catch(e){return e.message}}", {"id": fid, "asset": bad})
            assert "URI" in error
            assert page.evaluate("JSON.stringify(Floorplan.exportPlan())") == before
            bad = fixture_asset("BadBounds", normalized=False)
            error = page.evaluate("async v=>{try{await Floorplan.attachFurnitureAsset(v.id,v.asset);return null}catch(e){return e.message}}", {"id": fid, "asset": bad})
            assert "归一化" in error
            assert page.evaluate("JSON.stringify(Floorplan.exportPlan())") == before
            bad = fixture_asset("TooManyFaces", too_many_triangles=True)
            error = page.evaluate("v=>{try{Floorplan.validateFurnitureAsset(v);return null}catch(e){return e.message}}", bad)
            assert "250000" in error
            assert page.evaluate("JSON.stringify(Floorplan.exportPlan())") == before
            checks.append("外部资源、超限三角面及错误规范bbox资产被拒绝且不污染计划")
            page.evaluate("()=>{window.qaStorageSet=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException('QA quota failure','QuotaExceededError')};}")
            page.locator("#fH").fill("1120"); page.locator("#fH").press("Tab")
            assert not page.evaluate("Floorplan.persistence().saved")
            assert page.locator("#storageNotice").is_visible()
            page.evaluate("()=>{Storage.prototype.setItem=window.qaStorageSet;delete window.qaStorageSet;}")
            page.locator("#fH").fill("1130"); page.locator("#fH").press("Tab")
            assert page.evaluate("Floorplan.persistence().saved")
            assert not page.locator("#storageNotice").is_visible()
            checks.append("存储容量失败明确提示，恢复写入后提示消失")
            assert not errors, errors
            context.close(); browser.close()
        result = {"ok": True, "checks": checks, "pageErrors": errors}
        (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()


def run_targeted():
    output = ROOT / "outputs/furniture-assets-browser-targeted"
    output.mkdir(parents=True, exist_ok=True)
    asset, replacement, late = fixture_asset(), fixture_asset("QAReplacement"), fixture_asset("QALate")
    (output / "replacement.asset.json").write_text(json.dumps(replacement))
    plan = json.loads((ROOT / "examples/two-room.json").read_text())
    plan["id"] = "asset-targeted-qa"
    plan["furnitureAssets"] = {asset["id"]: asset}
    target = plan["furniture"][0]
    target.update(assetId=asset["id"], height=777)
    fid = target["id"]
    TOOLS.build(plan, output / "index.html")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--ignore-certificate-errors"])
            context = browser.new_context(viewport={"width": 1500, "height": 1000}, ignore_https_errors=True)
            configure_browser_runtime(context)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}/outputs/furniture-assets-browser-targeted/index.html")
            page.wait_for_function("!!window.Floorplan && !!window.View3D", timeout=90000)
            page.locator(f'[data-fid="{fid}"]').click()
            page.locator("#fAssetFile").set_input_files(str(output / "replacement.asset.json"))
            page.wait_for_function("id=>Floorplan.exportPlan().furniture.find(f=>f.id===id)?.assetId==='QAReplacement'", arg=fid)
            assert page.locator("#fH").input_value() == "777"
            checks.append("真实资产文件替换保留用户已有高度777mm")
            page.locator('[data-view="3d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            page.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='ready'", arg=fid)
            current = page.evaluate("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)", fid)
            assert abs(current["heightMm"] - 777) < .1
            page.locator("#fAssetRestore").click()
            page.locator("#fAssetFile").set_input_files(str(output / "replacement.asset.json"))
            page.wait_for_function("id=>Floorplan.exportPlan().furniture.find(f=>f.id===id)?.assetId==='QAReplacement'", arg=fid)
            assert page.evaluate("id=>Floorplan.exportPlan().furniture.find(f=>f.id===id).height", fid) == 900
            checks.append("没有已有高度时采用资产默认900mm")
            result = page.evaluate("""async v=>{
              const pending=Floorplan.attachFurnitureAsset(v.id,v.asset);
              const changed=Floorplan.exportPlan();changed.id='changed-during-load';Floorplan.loadPlan(changed);
              let error=null;try{await pending;}catch(e){error=e.message;}
              return {error,plan:Floorplan.exportPlan()};
            }""", {"id": fid, "asset": late})
            assert "已变化" in result["error"]
            assert result["plan"]["id"] == "changed-during-load"
            assert "QALate" not in result["plan"]["furnitureAssets"]
            checks.append("异步加载期间切换计划不污染新计划同ID家具")
            page.evaluate("p=>Floorplan.loadPlan(p)", plan)
            result = page.evaluate("""async v=>{
              const pending=Floorplan.attachFurnitureAsset(v.id,v.asset);
              select({kind:'furn',id:v.id});deleteSel();
              let error=null;try{await pending;}catch(e){error=e.message;}
              return {error,count:Floorplan.exportPlan().furniture.filter(f=>f.id===v.id).length};
            }""", {"id": fid, "asset": late})
            assert "已变化" in result["error"] and result["count"] == 0
            checks.append("异步加载期间删除家具不会复活或替换目标")
            page.evaluate("p=>Floorplan.loadPlan(p)", plan)
            result = page.evaluate("""async v=>{
              const pending=Floorplan.attachFurnitureAsset(v.id,v.asset);
              document.querySelector('#undo').click();
              let error=null;try{await pending;}catch(e){error=e.message;}
              return {error,plan:Floorplan.exportPlan()};
            }""", {"id": fid, "asset": late})
            assert "已变化" in result["error"]
            assert "QALate" not in result["plan"]["furnitureAssets"]
            checks.append("异步加载期间撤销不污染回退状态")
            bad = fixture_asset("BadBounds", normalized=False)
            bad_plan = json.loads(json.dumps(plan))
            bad_plan["furnitureAssets"][bad["id"]] = bad
            bad_plan["furniture"][0]["assetId"] = bad["id"]
            page.evaluate("p=>Floorplan.loadPlan(p)", bad_plan)
            page.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='error'", arg=fid)
            assert page.locator(f'[data-fid="{fid}"] image').count() == 0
            assert page.locator(f'[data-fid="{fid}"] rect').count() > 0
            assert page.locator("#assetNotice").is_visible()
            assert page.evaluate("d=>!!View3D.assetLoadError(d)", bad["dataUrl"])
            page.locator('[data-view="2d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            page.screenshot(path=str(output / "fallback-2d.png"))
            checks.append("已知加载失败时2D和3D同时回退默认模型并显示失败原因")
            before = page.evaluate("JSON.stringify(Floorplan.exportPlan())")
            too_short = json.loads(json.dumps(asset)); too_short["defaultHeightMm"] = .5
            result = page.evaluate("v=>{try{Floorplan.validateFurnitureAsset(v);return null}catch(e){return e.message}}", too_short)
            assert "1～10000" in result
            too_short_plan = json.loads(json.dumps(plan)); too_short_plan["furniture"][0]["height"] = .5
            result = page.evaluate("p=>{try{Floorplan.loadPlan(p);return null}catch(e){return e.message}}", too_short_plan)
            assert "1～10000" in result
            assert page.evaluate("JSON.stringify(Floorplan.exportPlan())") == before
            checks.append("资产和实例高度均拒绝小于1mm的值且不污染计划")
            assert not errors, errors
            context.close(); browser.close()
        result = {"ok": True, "checks": checks, "pageErrors": errors}
        (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()


def run_bad_png():
    output = ROOT / "outputs/furniture-assets-browser-bad-png"
    output.mkdir(parents=True, exist_ok=True)
    asset = fixture_asset()
    bad = json.loads(json.dumps(asset))
    bad["id"] = "BadPngHeaderOnly"
    # 完整合法IHDR头及CRC，但刻意没有IDAT和IEND；头部检查不足以发现。
    png = base64.b64decode(asset["topViewDataUrl"].split(",", 1)[1])[:33]
    bad["topViewDataUrl"] = "data:image/png;base64," + base64.b64encode(png).decode()
    plan = json.loads((ROOT / "examples/two-room.json").read_text())
    plan["id"] = "asset-bad-png-qa"
    plan["furnitureAssets"] = {asset["id"]: asset}
    fid = plan["furniture"][0]["id"]
    plan["furniture"][0].update(assetId=asset["id"], height=777)
    TOOLS.build(plan, output / "index.html")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--ignore-certificate-errors"])
            context = browser.new_context(viewport={"width": 1500, "height": 1000}, ignore_https_errors=True)
            configure_browser_runtime(context)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}/outputs/furniture-assets-browser-bad-png/index.html")
            page.wait_for_function("!!window.Floorplan && !!window.View3D", timeout=90000)
            page.locator('[data-view="3d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            page.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='ready'", arg=fid)
            before = page.evaluate("JSON.stringify(Floorplan.exportPlan())")
            error = page.evaluate("async v=>{try{await Floorplan.attachFurnitureAsset(v.id,v.asset);return null}catch(e){return e.message}}", {"id": fid, "asset": bad})
            assert "PNG" in error and "解码" in error, error
            assert page.evaluate("JSON.stringify(Floorplan.exportPlan())") == before
            assert page.evaluate("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id).assetStatus", fid) == "ready"
            assert page.locator(f'[data-fid="{fid}"] image').count() == 1
            checks.append("相同GLB已缓存时，只有IHDR的坏PNG仍被真实解码拒绝且不污染原计划")
            assert page.evaluate("v=>!View3D.assetLoadError(v.dataUrl,v.topViewDataUrl)", asset)
            assert page.evaluate("v=>!!View3D.assetLoadError(v.dataUrl,v.topViewDataUrl)", bad)
            checks.append("缓存按GLB和PNG组合隔离，坏PNG不污染共用同一GLB的有效资产")
            bad_plan = json.loads(json.dumps(plan))
            bad_plan["furnitureAssets"] = {bad["id"]: bad}
            bad_plan["furniture"][0]["assetId"] = bad["id"]
            page.evaluate("p=>Floorplan.loadPlan(p)", bad_plan)
            page.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='error'", arg=fid)
            assert page.locator(f'[data-fid="{fid}"] image').count() == 0
            assert page.locator(f'[data-fid="{fid}"] rect').count() > 0
            assert page.locator("#assetNotice").is_visible()
            assert "PNG" in page.locator("#assetNotice").inner_text()
            page.locator('[data-view="2d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            page.screenshot(path=str(output / "bad-png-fallback-2d.png"))
            checks.append("计划内嵌坏PNG时2D与3D同时回退默认模型并显示PNG解码错误")
            page.evaluate("p=>Floorplan.loadPlan(p)", plan)
            page.locator('[data-view="3d"]').click()
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            page.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='ready'", arg=fid)
            assert page.locator(f'[data-fid="{fid}"] image').count() == 1
            assert not page.locator("#assetNotice").is_visible()
            checks.append("换回有效PNG后2D与3D图片模型恢复且清除错误提示")
            # 新页面从未初始化3D；只靠2D SVG的真实error处理也必须提示与回退。
            cold = context.new_page()
            cold.on("pageerror", lambda e: errors.append(str(e)))
            cold.goto(f"http://127.0.0.1:{server.server_port}/outputs/furniture-assets-browser-bad-png/index.html")
            cold.wait_for_function("!!window.Floorplan && !!window.View3D", timeout=90000)
            cold_bad = json.loads(json.dumps(bad))
            cold_bad["id"] = "ColdBadPng"
            cold_plan = json.loads(json.dumps(plan))
            cold_plan["furnitureAssets"] = {cold_bad["id"]: cold_bad}
            cold_plan["furniture"][0]["assetId"] = cold_bad["id"]
            cold.evaluate("p=>Floorplan.loadPlan(p)", cold_plan)
            cold.wait_for_function("id=>!document.querySelector(`[data-fid=\"${id}\"] image`)", arg=fid)
            assert cold.locator(f'[data-fid="{fid}"] rect').count() > 0
            assert cold.locator("#assetNotice").is_visible()
            assert "PNG" in cold.locator("#assetNotice").inner_text()
            assert cold.evaluate("View3D.diagnostics().furnitureBounds.length") == 0
            assert cold.evaluate("v=>View3D.assetLoadError(v.dataUrl,v.topViewDataUrl)===null", cold_bad)
            cold.screenshot(path=str(output / "bad-png-cold-2d-fallback.png"))
            cold.locator('[data-view="3d"]').click()
            cold.wait_for_function("!document.querySelector('#stage').classList.contains('animating')", timeout=30000)
            cold.wait_for_function("id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id)?.assetStatus==='error'", arg=fid)
            checks.append("从未进入3D的2D页面也会立即发现坏PNG、回退参数符号并提示，随后3D同样回退")
            cold.close()
            assert not errors, errors
            context.close(); browser.close()
        result = {"ok": True, "checks": checks, "pageErrors": errors}
        (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()


if __name__ == "__main__":
    import sys
    if "--bad-png" in sys.argv:
        run_bad_png()
    elif "--targeted" in sys.argv:
        run_targeted()
    else:
        run()
