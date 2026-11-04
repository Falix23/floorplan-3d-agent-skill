"""独立浏览器集成验收，不使用任何登录资料。"""
import functools
import http.server
import importlib.util
import json
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from browser_runtime import configure_browser_runtime

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("plan_tools",ROOT/"skills/floorplan-image/scripts/plan_tools.py")
tools=importlib.util.module_from_spec(spec);spec.loader.exec_module(tools)


def run():
    out=ROOT/"outputs";out.mkdir(exist_ok=True)
    plan=json.loads((ROOT/"examples/two-room.json").read_text())
    tools.build(plan,out/"two-room.html")
    server=http.server.ThreadingHTTPServer(("127.0.0.1",0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url="http://127.0.0.1:%d"%server.server_port
    errors=[];checks=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-webgl","--ignore-certificate-errors"])
            context=browser.new_context(viewport={"width":1500,"height":1000},ignore_https_errors=True,accept_downloads=True)
            configure_browser_runtime(context)
            page=context.new_page();page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(url+"/outputs/two-room.html")
            page.wait_for_function("window.Floorplan && document.querySelectorAll('#gRooms polygon.room').length===2")
            assert page.locator("#gFurn > g.furn").count()==8
            checks.append("自定义两房间八件家具加载")
            page.wait_for_function("!!window.View3D",timeout=90000)
            page.screenshot(path=str(out/"two-room-2d.png"))
            # 通过真实UI编辑家具，随后撤销。
            page.locator('[data-fid="I1"]').click()
            page.locator('#fW').fill('1600');page.locator('#fW').press('Tab')
            assert page.evaluate("Floorplan.exportPlan().furniture.find(f=>f.id==='I1').w")==1600
            assert page.evaluate("Floorplan.exportPlan().furniture.find(f=>f.id==='I1').placement.mode")=="free"
            box=page.locator('[data-fid="I1"]').bounding_box()
            x,y=box['x']+box['width']/2,box['y']+box['height']/2
            before=page.evaluate("Floorplan.exportPlan().furniture.find(f=>f.id==='I1').cx")
            page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+35,y+20,steps=8);page.mouse.up()
            assert page.evaluate("Floorplan.exportPlan().furniture.find(f=>f.id==='I1').cx")!=before
            page.locator('#undo').click()
            page.locator('#aRot').click()
            assert page.evaluate("Floorplan.exportPlan().furniture.find(f=>f.id==='I1').rot")==90
            page.locator('#undo').click()
            assert page.evaluate("Floorplan.exportPlan().furniture.find(f=>f.id==='I1').rot")==0
            checks.append("家具尺寸、旋转与撤销")
            # 使用几何编辑器真实改名和改变房间边界，再验证导出与刷新。
            page.locator('details.menu > summary').click();page.locator('#editPlan').click()
            edited=json.loads(page.locator('#planText').input_value())
            edited['title']='已编辑户型';edited['rooms'][0]['name']='多功能客厅'
            edited['rooms'][1]['poly'][2][1]=4800;edited['rooms'][1]['poly'][3][1]=4800
            page.locator('#planText').fill(json.dumps(edited,ensure_ascii=False));page.locator('#applyPlan').click()
            assert page.locator('#planEditor').evaluate('(e)=>!e.open')
            assert page.evaluate("Floorplan.exportPlan().rooms[1].poly[2][1]")==4800
            checks.append("完整几何编辑器应用")
            page.reload();page.wait_for_function("window.Floorplan && Floorplan.exportPlan().title==='已编辑户型'")
            assert page.evaluate("Floorplan.exportPlan().furniture[0].w")==1600
            checks.append("刷新恢复建筑与家具")
            page.locator('details.menu > summary').click()
            with page.expect_download() as download:
                page.locator('#exportJson').click()
            download.value.save_as(str(out/'browser-export.json'))
            exported=json.loads((out/'browser-export.json').read_text());assert exported['plan']['rooms'][1]['poly'][2][1]==4800
            page.evaluate('p=>Floorplan.loadPlan(p)',plan)
            page.locator('#fileIn').set_input_files(str(out/'browser-export.json'))
            page.wait_for_function("Floorplan.exportPlan().title==='已编辑户型'")
            checks.append("完整JSON导出再导入")
            # 浏览器导入拒绝会堵门的墙体，不污染当前状态。
            bad=json.loads(json.dumps(plan));bad['walls'].append([4000,3400,4240,4300,'u'])
            result=page.evaluate("p=>{try{Floorplan.loadPlan(p);return false}catch(e){return e.message}}",bad)
            assert '洞口' in result
            assert page.evaluate("Floorplan.exportPlan().title")=="已编辑户型"
            checks.append("非法导入不污染现有状态")
            # 3D通过实际WebGL场景验收。
            page.wait_for_function('!!window.View3D',timeout=90000)
            page.locator('[data-view="3d"]').click()
            page.wait_for_selector('body.m3d',timeout=30000)
            page.wait_for_function("!!document.querySelector('#view3d canvas')")
            page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')",timeout=30000)
            assert page.locator('#roomList [data-room]').count()==3
            page.screenshot(path=str(out/'two-room-3d.png'))
            checks.append("自定义户型WebGL 3D场景")
            page.locator('#vTop').click()
            page.wait_for_function("View3D.diagnostics().projection==='orthographic'")
            bounds=page.evaluate('View3D.diagnostics().furnitureBounds')
            current=page.evaluate('Floorplan.exportPlan().furniture')
            import math
            for f in current:
                b=next(b for b in bounds if b['id']==f['id'])
                c,s=abs(math.cos(math.radians(f['rot']))),abs(math.sin(math.radians(f['rot'])))
                hw,hh=(f['w']*c+f['d']*s)/2,(f['w']*s+f['d']*c)/2
                assert abs(b['min'][0]-(f['cx']-hw))<.1,(f,b)
                assert abs(b['min'][1]-(f['cy']-hh))<.1,(f,b)
                assert abs(b['max'][0]-(f['cx']+hw))<.1,(f,b)
                assert abs(b['max'][1]-(f['cy']+hh))<.1,(f,b)
                # 包含倾斜椅腿的自制chair夹具，不能用近似包围盒或放宽容差掩盖误差。
                assert abs(b['bottomMm'])<.1,(f,b)
            checks.append('正交俯视及3D真实顶点四边占地与2D一致，倾斜椅腿准确落地')
            page.locator('#vIso').click()
            # 3D激活时切换另一户型，标签和家具也应一起重建。
            changed=json.loads(json.dumps(plan));changed['id']='other';changed['height']=3200;changed['furniture'][0].pop('color');changed['rooms'][0]['name']='新方案';changed['furniture']=changed['furniture'][:3]
            page.evaluate('p=>Floorplan.loadPlan(p)',changed)
            page.wait_for_function("document.querySelector('#roomList').textContent.includes('新方案')")
            assert page.evaluate('Floorplan.exportPlan().furniture.length')==3
            assert page.locator('[data-cut="full"]').evaluate("e=>e.classList.contains('on')")
            page.locator('[data-cut="1.2"]').click();page.locator('[data-cut="full"]').click()
            assert page.locator('[data-cut="full"]').evaluate("e=>e.classList.contains('on')")
            assert page.evaluate("Floorplan.exportPlan().furniture[0].color").startswith('#')
            checks.append("3D激活期间更换户型、动态层高和默认家具颜色")
            # 原始示例仍可打开，不依赖新样例的缓存。
            page.goto(url+'/index.html');page.wait_for_function('!!window.Floorplan')
            assert page.evaluate('Floorplan.exportPlan().rooms.length')==13
            checks.append("上游示例启动")
            # 手机视口确保基础交互可见。
            page.set_viewport_size({'width':390,'height':844});page.goto(url+'/outputs/two-room.html')
            page.wait_for_function('!!window.Floorplan')
            page.screenshot(path=str(out/'two-room-mobile.png'))
            checks.append("手机视口页面加载")
            rebuilt=json.loads(json.dumps(plan));rebuilt['title']='新生成版本'
            tools.build(rebuilt,out/'two-room.html')
            page.goto(url+'/outputs/two-room.html?revision=new')
            page.wait_for_function("Floorplan.exportPlan().title==='新生成版本'")
            checks.append('重新生成的计划不被旧编辑缓存覆盖')
            tools.build(plan,out/'two-room.html')
            assert not errors,errors
            context.close();browser.close()
        result={'ok':True,'checks':checks,'page_errors':errors}
        (out/'browser-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
    finally:
        server.shutdown()

if __name__=='__main__':run()
