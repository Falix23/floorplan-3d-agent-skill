"""多层真实WebGL/编辑/保存回归，使用自制输入，不使用账号或第三方图片。"""
import copy
import functools
import http.server
import importlib.util
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright
from browser_runtime import configure_browser_runtime
from browser_furniture_assets import fixture_asset

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('plan_tools',ROOT/'skills/floorplan-image/scripts/plan_tools.py')
tools=importlib.util.module_from_spec(spec);spec.loader.exec_module(tools)


def run():
    output=ROOT/'outputs/multi-floor/2026-11-06';output.mkdir(parents=True,exist_ok=True)
    plan=json.loads((ROOT/'examples/multi-floor-duplex.json').read_text())
    # 本地来源网格夹具用于检测每层资产的同名实例隔离，不作为真实照片匹配证据。
    plan['id']='multi-floor-browser-qa';asset=fixture_asset('MultiBox')
    p=plan['floors'][1]['plan'];p['furnitureAssets']={asset['id']:asset};p['furniture'][0].update(assetId=asset['id'],height=900)
    tools.build(plan,output/'browser-case.html')
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    errors=[];checks=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--ignore-certificate-errors'])
            context=browser.new_context(viewport={'width':1500,'height':1000},accept_downloads=True)
            configure_browser_runtime(context);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            url=f'http://127.0.0.1:{server.server_port}/outputs/multi-floor/2026-11-06/browser-case.html'
            page.goto(url);page.wait_for_function('!!window.Floorplan&&!!window.View3D',timeout=60000)
            assert page.locator('#floorSelect option').count()==2
            assert page.evaluate('Floorplan.exportPlan().floors.length')==2
            checks.append('多层JSON加载与楼层选择')
            page.locator('[data-fid="I1"]').click();page.locator('#fW').fill('2100');page.locator('#fW').press('Tab')
            page.locator('#floorSelect').select_option('F2')
            assert page.evaluate('Floorplan.activePlan().furniture[0].w')==1800
            assert page.locator('[data-fid="I1"] image').count()==1
            assert page.locator('#gRooms path.room').count()==1
            assert page.locator('#subtitle').inner_text().find('56.72')>=0
            checks.append('各层同名家具隔离、自定义资产与洞口净面积')
            page.locator('#floorUnderlay').click()
            assert page.locator('#gUnderlay path').count()==3
            page.screenshot(path=str(output/'duplex-floor2-2d.png'))
            page.locator('[data-fid="I1"]').click();page.locator('#fW').fill('1700');page.locator('#fW').press('Tab')
            page.locator('#floorSelect').select_option('F1')
            assert page.evaluate('Floorplan.activePlan().furniture[0].w')==2100
            page.locator('#floorSelect').select_option('F2');page.locator('#undo').click()
            # 撤销上层编辑时保持楼层身份和下层已完成修改。
            assert page.evaluate('Floorplan.activeFloor()')=='F2'
            assert page.evaluate('Floorplan.activePlan().furniture[0].w')==1800
            page.locator('#redo').click()
            assert page.evaluate('Floorplan.exportPlan().floors[0].plan.furniture[0].w')==2100
            checks.append('跨层切换、撤销重做与下层轮廓')
            page.reload();page.wait_for_function("!!window.Floorplan&&Floorplan.activeFloor()==='F2'")
            assert page.evaluate('Floorplan.activePlan().furniture[0].w')==1700
            checks.append('刷新恢复当前层与全部楼层修改')
            page.locator('[data-view="3d"]').click();page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            page.wait_for_function("View3D.diagnostics().furnitureBounds.find(f=>f.id==='I1').assetStatus==='ready'")
            page.locator('#buildingMode').select_option('stacked');page.wait_for_timeout(1200)
            page.wait_for_function("View3D.buildingDiagnostics().floors[1].furniture[0].status==='ready'")
            d=page.evaluate('View3D.buildingDiagnostics()');assert [f['displayElevationMm'] for f in d['floors']]==[0,3000]
            assert not page.evaluate("View3D.floorProbe('F2',7200,6500)")
            assert page.evaluate("View3D.floorProbe('F2',2500,2000)")
            checks.append('真实WebGL叠层标高、跨层资产加载、楼板洞口射线验证')
            page.screenshot(path=str(output/'duplex-stacked-3d.png'))
            page.locator('#buildingMode').select_option('exploded');page.wait_for_timeout(1200)
            d=page.evaluate('View3D.buildingDiagnostics()');assert d['floors'][1]['displayElevationMm']==4800
            assert page.evaluate('Floorplan.exportPlan().floors[1].elevation')==3000
            page.screenshot(path=str(output/'duplex-exploded-3d.png'))
            checks.append('分层展开只影响展示，不改变真实标高')
            page.locator('#buildingMode').select_option('single');page.wait_for_timeout(1200)
            page.locator('#vTop').click();page.wait_for_function("View3D.diagnostics().projection==='orthographic'")
            page.locator('#vIso').click();page.wait_for_timeout(1200)
            page.evaluate("select({kind:'furn',id:'I1'})")
            page.locator('#fW').fill('1600');page.locator('#fW').press('Tab')
            page.wait_for_function("View3D.diagnostics().furnitureBounds.find(f=>f.id==='I1').assetStatus==='ready'")
            assert page.evaluate('Floorplan.activePlan().furniture[0].w')==1600
            # 部分材质改色，资产保存到当前楼层，其他楼层不受污染。
            materials=page.evaluate("View3D.materials('I1')")
            idx=materials[0]['index']
            page.evaluate("async i=>await Floorplan.applyFurnitureMaterial('I1',{schemaVersion:1,materials:[{index:i,color:'#334455',roughness:.8,textureMode:'solid',evidence:'Self-authored multi-floor QA color edit'}]})",idx)
            assert page.evaluate('Floorplan.exportPlan().floors[0].plan.furniture[0].assetId||null') is None
            assert page.evaluate('Floorplan.activePlan().furniture[0].assetId')!='MultiBox'
            checks.append('当前层3D尺寸/分材质编辑、相同家具ID不串层')
            page.evaluate("select({kind:'furn',id:'I1'})")
            page.locator('#fR').fill('90');page.locator('#fR').press('Tab')
            page.locator('#fX').fill('2500');page.locator('#fX').press('Tab')
            assert page.evaluate('Floorplan.activePlan().furniture[0].rot')==90
            assert page.evaluate('Floorplan.exportPlan().floors[0].plan.furniture[0].rot')==0
            checks.append('上层家具位置和朝向编辑不改变下层同ID家具')
            page.locator('details.menu > summary').click()
            with page.expect_download() as downloaded:page.locator('#exportJson').click()
            downloaded.value.save_as(str(output/'browser-export.json'))
            current=page.evaluate('Floorplan.exportPlan()')
            fresh=browser.new_context(viewport={'width':1500,'height':1000});configure_browser_runtime(fresh)
            q=fresh.new_page();q.goto(url);q.wait_for_function('!!window.Floorplan')
            q.on('pageerror',lambda e:errors.append(str(e)))
            q.locator('#fileIn').set_input_files(str(output/'browser-export.json'))
            q.wait_for_function("Floorplan.activePlan().furniture[0].w===1600")
            assert q.evaluate('Floorplan.exportPlan()')==current
            q.reload();q.wait_for_function("!!window.Floorplan&&Floorplan.activePlan().furniture[0].w===1600")
            assert q.evaluate('Floorplan.exportPlan()')==current
            checks.append('全新浏览器会话导入完整多层状态、修改资产与刷新一致')
            bad=copy.deepcopy(current);bad['floors'][1]['plan']['rooms'][2].pop('holes')
            message=q.evaluate("p=>{try{Floorplan.loadPlan(p);return ''}catch(e){return e.message}}",bad)
            assert '楼板封住' in message;assert q.evaluate('Floorplan.exportPlan()')==current
            checks.append('堵塞楼梯的建筑导入被拒绝且不污染现有状态')
            tampered=json.loads((output/'browser-export.json').read_text())
            tampered['floorStates']['F1']['plan']['rooms'][0]['holes']=[[[1000,500],[3500,500],[3500,2000],[1000,2000]]]
            # 活动层之外损坏的运行时状态也不能通过仅校验建筑原始副本蒙混导入。
            (output/'invalid-state.json').write_text(json.dumps(tampered));q.locator('#fileIn').set_input_files(str(output/'invalid-state.json'))
            q.wait_for_function("document.querySelector('#toast').textContent.includes('洞口')")
            assert q.evaluate('Floorplan.exportPlan()')==current
            checks.append('隐藏楼层的非法编辑备份导入被拒绝')
            q.set_viewport_size({'width':390,'height':844});q.locator('#floorSelect').select_option('F1')
            assert q.locator('#floorSelect').is_visible();q.screenshot(path=str(output/'mobile-2d.png'))
            checks.append('手机楼层切换')
            q.set_viewport_size({'width':1500,'height':1000})
            villa=json.loads((ROOT/'examples/multi-floor-villa.json').read_text());villa['floors'][2]['offset']=[400,0]
            q.evaluate('p=>Floorplan.loadPlan(p)',villa)
            assert q.locator('#floorSelect option').count()==3
            q.locator('[data-view="3d"]').click();q.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            q.locator('#buildingMode').select_option('exploded');q.wait_for_timeout(1500)
            assert [f['elevation'] for f in q.evaluate('View3D.buildingDiagnostics().floors')]==[-3000,0,3000]
            assert q.evaluate('View3D.buildingDiagnostics().floors[2].offsetMm')==[400,0]
            q.screenshot(path=str(output/'villa-three-levels.png'))
            checks.append('地下室＋两层，负标高与三层展开')
            treads=copy.deepcopy(plan)
            treads['stairs']=[{'id':'S1','name':'显式踏步夹具','fromFloorId':'F1','toFloorId':'F2','treads':[
                {'poly':[[6750,4500+i*500],[7650,4500+i*500],[7650,5000+i*500],[6750,5000+i*500]],'rise':500} for i in range(6)]}]
            q.evaluate('p=>Floorplan.loadPlan(p)',treads)
            assert q.locator('#gStairs path').count()==6
            q.locator('#buildingMode').select_option('stacked');q.wait_for_timeout(1100)
            stair=q.evaluate('View3D.buildingDiagnostics().floors[0].stairs[0]')
            assert stair['meshCount']==6
            assert abs(stair['topDisplayMm']-3000)<.1
            assert not q.evaluate("View3D.floorProbe('F2',7200,6500)")
            checks.append('显式多边形踏步在2D/实际网格中一致，连续连接并到达上层标高')
            # 中间层同时显示两段连接；方向说明须分行，不能在同一位置叠字。
            connections=copy.deepcopy(plan)
            third=copy.deepcopy(connections['floors'][1]);third.update(id='F3',name='三层',elevation=6000)
            connections['floors'].append(third)
            second=copy.deepcopy(connections['stairs'][0]);second.update(id='S2',fromFloorId='F2',toFloorId='F3')
            connections['stairs'].append(second)
            q.locator('[data-view="2d"]').click();q.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
            q.evaluate('p=>Floorplan.loadPlan(p)',connections);q.locator('#floorSelect').select_option('F2')
            labels=q.locator('#gStairs text').evaluate_all("nodes=>nodes.map(n=>{const b=n.getBBox();return {direction:n.dataset.stairDirection,text:n.lastChild.textContent,x:b.x,y:b.y,w:b.width,h:b.height}})")
            assert len(labels)==2 and {x['direction'] for x in labels}=={'down','up'}
            a,b=labels
            assert a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y'],labels
            assert '三层' in next(x['text'] for x in labels if x['direction']=='up')
            checks.append('中间楼层上下梯向使用目标楼层短标签，实际SVG文字不重叠')
            assert not errors,errors
            fresh.close();context.close();browser.close()
        report={'ok':True,'checks':checks,'pageErrors':errors,'realPhotoRecognitionValidated':False,'inputs':'self-authored architectural test fixtures'}
        (output/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False))
    finally:server.shutdown()


if __name__=='__main__':run()
