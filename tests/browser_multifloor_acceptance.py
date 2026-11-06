"""参考openPlan3D验收维度，自写测试；不运行或复制其生产实现。"""
import copy
import base64
import functools
import http.server
import importlib.util
import io
import json
from pathlib import Path
import threading

from PIL import Image

from playwright.sync_api import sync_playwright
from browser_runtime import configure_browser_runtime

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plan_tools', ROOT/'skills/floorplan-image/scripts/plan_tools.py')
tools = importlib.util.module_from_spec(spec); spec.loader.exec_module(tools)


def fixture():
    source = json.loads((ROOT/'examples/multi-floor-duplex.json').read_text())
    floors = []
    for i, (fid, elevation, offset) in enumerate([
        ('B1',-3500,[-200,100]), ('F1',0,[0,0]), ('F2',4250,[300,-200]), ('F4',11000,[500,-350])
    ]):
        f = copy.deepcopy(source['floors'][1 if fid=='F2' else 0])
        f.update(id=fid,name=fid,elevation=elevation,offset=offset)
        if fid in ('F1','F2'):
            stream=io.BytesIO();Image.new('RGB',(10,10),'blue' if fid=='F1' else 'red').save(stream,format='PNG')
            f['plan']['reference']={'width':10,'height':10,'mmPerPixel':100,'originPx':[0,0],
                'dataUrl':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}
        if fid=='F4':
            f['plan'].update(id='empty-furniture-floor',title='L形顶层',
                rooms=[{'id':'R1','name':'测试L形','mat':'wood','poly':[[0,0],[6000,0],[6000,2500],[3000,2500],[3000,5000],[0,5000]],'at':[1500,2000]}],
                walls=[],doors=[],windows=[],slides=[],furniture=[])
        floors.append(f)
    # 楼层顺序故意打乱，不能拿数组下标当标高。
    return dict(source,id='multifloor-reference-acceptance',floors=[floors[i] for i in [3,0,2,1]],stairs=[])


def run():
    output = ROOT/'outputs/multi-floor/2026-11-06/reference-alignment'
    output.mkdir(parents=True,exist_ok=True)
    building = fixture(); tools.build(building,output/'acceptance.html')
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0),
        functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    checks, errors = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-webgl'])
            context = browser.new_context(viewport={'width':1500,'height':1000},accept_downloads=True)
            configure_browser_runtime(context); page = context.new_page(); page.on('pageerror',lambda e:errors.append(str(e)))
            url = f'http://127.0.0.1:{server.server_port}/outputs/multi-floor/2026-11-06/reference-alignment/acceptance.html'
            page.goto(url); page.wait_for_function('!!window.Floorplan&&!!window.View3D')
            assert page.locator('#floorSelect option').evaluate_all('nodes=>nodes.map(n=>n.value)')==['B1','F1','F2','F4']
            page.locator('#floorUnderlay').click()
            assert page.locator('#gUnderlay g').get_attribute('transform')=='translate(-200 150)'
            assert page.locator('#gUnderlay path').count()==3
            page.locator('#floorSelect').select_option('B1'); assert page.locator('#gUnderlay path').count()==0
            checks.append('乱序楼层、负标高及缺号楼层的排序和最近下层轮廓一致')
            page.locator('#floorSelect').select_option('F2'); page.locator('[data-fid="I1"]').click()
            page.locator('#refToggle').click()
            reference=page.locator('#gReference image').get_attribute('href')
            page.locator('#floorSelect').select_option('F1')
            assert page.locator('#gReference image').get_attribute('href')!=reference
            page.locator('#floorSelect').select_option('F4')
            assert page.locator('#gSel').inner_html()==''
            assert page.locator('#gFurn > g.furn').count()==0
            assert page.locator('#gRooms .room').count()==1
            assert page.locator('#refToggle').evaluate('n=>n.hidden')
            assert page.locator('#gReference image').count()==0
            checks.append('非首层缺省可选资产/参考图且无家具时不泄漏上一层选择、家具或图源')
            for width in [1500,390]:
                page.set_viewport_size({'width':width,'height':1000})
                for fid in ['F4','B1','F2','F1']:
                    page.locator('#floorSelect').select_option(fid)
                    bounds = [float(x) for x in page.locator('#plan').get_attribute('viewBox').split()]
                    plan = page.evaluate('Floorplan.activePlan()')
                    assert all(bounds[0]<=x<=bounds[0]+bounds[2] and bounds[1]<=y<=bounds[1]+bounds[3]
                               for r in plan['rooms'] for x,y in r['poly'])
            checks.append('桌面和手机逐层自动适配视野，L形与其他楼层均在可视范围')
            page.set_viewport_size({'width':1500,'height':1000})
            page.locator('[data-view="3d"]').click(); page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            page.locator('#buildingMode').select_option('stacked')
            ordered = sorted(building['floors'],key=lambda f:f['elevation'])
            for fid in ['F4','B1','F2','F1']:
                page.locator('#floorSelect').select_option(fid); page.wait_for_timeout(300)
                diag = page.evaluate('View3D.buildingDiagnostics()')
                assert [f['floorId'] for f in diag['floors']]==[f['id'] for f in ordered]
                for actual, expected in zip(diag['floors'],ordered):
                    assert abs(actual['displayElevationMm']-(expected['elevation']+3500))<.01
                    assert actual['offsetMm']==expected['offset']
                assert page.evaluate("View3D.floorProbe('F4',1500,3650)")
                assert not page.evaluate("View3D.floorProbe('F4',5500,3650)")
            page.screenshot(path=str(output/'mixed-height-stack.png'))
            checks.append('不同活动层不改变四层真实叠放，负标高、非等层高、偏移和L形缺角网格正确')
            page.locator('[data-view="2d"]').click(); page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            page.locator('#floorSelect').select_option('F2')
            before = page.evaluate('Floorplan.exportPlan()')
            net_before = page.evaluate('ROOMS.reduce((a,r)=>a+roomArea(r),0)')
            page.locator('details.menu > summary').click(); page.locator('#editPlan').click()
            edited = json.loads(page.locator('#planText').input_value())
            upper = next(f for f in edited['floors'] if f['id']=='F2')
            upper['plan']['rooms'][2].pop('holes')
            page.locator('#planText').fill(json.dumps(edited,ensure_ascii=False)); page.locator('#applyPlan').click()
            page.locator('#floorSelect').select_option('F2')
            net_after = page.evaluate('ROOMS.reduce((a,r)=>a+roomArea(r),0)')
            assert abs(net_after-net_before-12.48)<.001
            assert all(f==next(v for v in before['floors'] if v['id']==f['id']) for f in page.evaluate('Floorplan.exportPlan().floors') if f['id']!='F2')
            page.locator('#undo').click(); assert page.evaluate('Floorplan.exportPlan()')==before
            page.locator('#redo').click(); assert page.evaluate('Floorplan.exportPlan()')==edited
            page.locator('#undo').click(); assert page.evaluate('Floorplan.exportPlan()')==before
            checks.append('洞口真实JSON编辑改变净面积且不改变其他楼层，一步撤销/重做保留原开口')
            page.locator('[data-view="3d"]').click(); page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            page.locator('#buildingMode').select_option('stacked'); page.wait_for_timeout(300)
            assert not page.evaluate("View3D.floorProbe('F2',7500,6300)")
            page.reload(); page.wait_for_function("!!window.Floorplan&&!!window.View3D")
            assert page.evaluate('Floorplan.exportPlan()')==before
            page.locator('[data-view="3d"]').click(); page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            page.locator('#buildingMode').select_option('stacked'); page.wait_for_timeout(300)
            assert not page.evaluate("View3D.floorProbe('F2',7500,6300)")
            checks.append('开口撤销后刷新与实际楼板射线保持一致，不只保留字段')
            saved = page.evaluate('Floorplan.exportPlan()')
            message = page.evaluate("()=>{try{Floorplan.setFloor('missing');return ''}catch(e){return e.message}}")
            assert message and page.evaluate('Floorplan.exportPlan()')==saved
            checks.append('无效切层拒绝且不破坏当前整栋状态')
            assert not errors,errors
            context.close(); browser.close()
        report = {'ok':True,'checks':checks,'pageErrors':errors,
                  'inspiration':'https://github.com/laanlabs/openPlan3D/tree/98f9cb467e4045c6abc5d9880ada1f03dff50c68/tests',
                  'notClaimed':['新增/复制/删除楼层UI','每层相机姿态恢复','完整楼梯净空与建筑规范'],
                  'fixture':'自制4层工程夹具，不是输入图识别精度测试'}
        (output/'openplan-inspired-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        server.shutdown()


if __name__ == '__main__':
    run()
