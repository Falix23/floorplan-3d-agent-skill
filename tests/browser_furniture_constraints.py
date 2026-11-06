"""真实SVG特征与Three.js床头/靠背/柜门网格的方向，不用旋转字段自证。"""
import copy,functools,http.server,importlib.util,json,math,threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from browser_runtime import configure_browser_runtime
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('plan_tools',ROOT/'skills/floorplan-image/scripts/plan_tools.py');tools=importlib.util.module_from_spec(spec);spec.loader.exec_module(tools)


def fixture():
 p={'schemaVersion':1,'id':'furniture-semantic-browser','title':'家具语义自制夹具','units':'mm','height':2800,
    'scale':{'status':'relative'},'furnitureReview':{'required':True},'rooms':[{'id':'R1','name':'方向检查','mat':'wood','poly':[[0,0],[20000,0],[20000,18000],[0,18000]]}],
    'walls':[],'doors':[],'windows':[],'slides':[],'furniture':[]}
 for row,kind in enumerate(['bed','sofa','wardrobe','bookshelf']):
  for col,(rot,front) in enumerate([(0,[0,1]),(90,[-1,0]),(180,[0,-1]),(270,[1,0])]):
   p['furniture'].append({'id':f'I{row*4+col+1}','type':kind,'name':kind,'cx':2500+col*4500,'cy':2500+row*4200,'w':1800,'d':2000 if kind=='bed' else 600 if kind in ('wardrobe','bookshelf') else 900,'rot':rot,
     'orientationConstraint':{'status':'observed','front':front,'feature':'自制已知床头/靠背/柜门','maxAngleDegrees':5,'evidence':'工程夹具，不冒充图片识别'},'placement':{'mode':'free','evidence':'夹具独立摆放'}})
 return p


def run():
 out=ROOT/'outputs/multi-floor/2026-11-06/furniture-constraints';out.mkdir(parents=True,exist_ok=True)
 p=fixture();tools.build(p,out/'semantic-fixture.html')
 server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT)));threading.Thread(target=server.serve_forever,daemon=True).start();checks=[];errors=[]
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-webgl']);context=browser.new_context(viewport={'width':1500,'height':1000},accept_downloads=True);configure_browser_runtime(context);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
   url=f'http://127.0.0.1:{server.server_port}/outputs/multi-floor/2026-11-06/furniture-constraints/semantic-fixture.html';page.goto(url);page.wait_for_function('!!window.Floorplan&&!!window.View3D')
   svg=page.evaluate("""()=>Array.from(document.querySelectorAll('#gFurn > .furn')).map(g=>{
    const part=g.querySelector('[data-semantic]'),b=part.getBBox(),p=new DOMPoint(b.x+b.width/2,b.y+b.height/2).matrixTransform(part.getScreenCTM()),o=new DOMPoint(0,0).matrixTransform(g.getScreenCTM()),back=part.dataset.semantic!=='cabinet-front',dx=back?o.x-p.x:p.x-o.x,dy=back?o.y-p.y:p.y-o.y,n=Math.hypot(dx,dy);return {id:g.dataset.fid,front:[dx/n,dy/n]};})""")
   for f in p['furniture']:
    a=next(x['front'] for x in svg if x['id']==f['id']);assert sum(x*y for x,y in zip(a,f['orientationConstraint']['front']))>.999999,(f,a)
   checks.append('四类家具×四方向，实际SVG床头/靠背/柜门特征正确')
   page.locator('[data-view="3d"]').click();page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
   directions=page.evaluate('View3D.semanticDirections()')
   for f in p['furniture']:
    actual=[x for x in directions if x['id']==f['id']];assert actual,f
    assert all(sum(x*y for x,y in zip(v['front'],f['orientationConstraint']['front']))>math.cos(math.radians(5)) for v in actual),(f,actual)
   page.screenshot(path=str(out/'semantic-fixture-3d.png'));checks.append('实际Three.js床头板/靠背/柜门面几何朝向与源约束一致，不只比较旋转字段或包围盒')
   before=page.evaluate('Floorplan.exportPlan()');bad=copy.deepcopy(before);bad['furniture'][0]['rot']=180
   message=page.evaluate("p=>{try{Floorplan.loadPlan(p);return ''}catch(e){return e.message}}",bad);assert '朝向' in message;assert page.evaluate('Floorplan.exportPlan()')==before
   checks.append('180度反向且外包框不变的方案被浏览器拒绝，当前方案不受污染')
   page.locator('[data-view="2d"]').click();page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
   page.locator('[data-fid="I1"]').click();page.locator('#fR').fill('90');page.locator('#fR').press('Tab')
   edited=page.evaluate('Floorplan.exportPlan()');assert edited['furniture'][0]['orientationConstraint']['status']=='released';assert edited['furniture'][0]['rot']==90
   page.reload();page.wait_for_function("!!window.Floorplan&&Floorplan.activePlan().furniture[0].rot===90");assert page.evaluate('Floorplan.exportPlan()')==edited
   checks.append('用户主动旋转保留历史并解除源图朝向约束，刷新不会吸回原图')
   chair=copy.deepcopy(p);chair['id']='chair-target';chair['furnitureReview']['requireFacingTargets']=True
   chair['furniture']=[{'id':'I1','type':'chair','name':'椅子','cx':5000,'cy':6000,'w':500,'d':500,'rot':180,'facingTarget':{'targetId':'I2','maxAngleDegrees':60,'evidence':'自制桌面在椅子北方'}}, {'id':'I2','type':'table','name':'桌','cx':5000,'cy':4000,'w':1500,'d':900,'rot':0}]
   page.evaluate('p=>Floorplan.loadPlan(p)',chair);page.locator('[data-fid="I2"]').click();page.locator('#fY').fill('10000');page.locator('#fY').press('Tab')
   current=page.evaluate('Floorplan.exportPlan()');f=current['furniture'][0];assert 'facingTarget' not in f and f['orientationConstraint']['status']=='released';assert f['previousFacingTarget']['targetId']=='I2'
   page.locator('details.menu > summary').click()
   with page.expect_download() as dl:page.locator('#exportJson').click()
   dl.value.save_as(str(out/'semantic-edited-export.json'));fresh=browser.new_context();configure_browser_runtime(fresh);q=fresh.new_page();q.goto(url);q.wait_for_function('!!window.Floorplan');q.locator('#fileIn').set_input_files(str(out/'semantic-edited-export.json'));q.wait_for_function("Floorplan.activePlan().id==='chair-target'");assert q.evaluate('Floorplan.exportPlan()')==current;fresh.close()
   checks.append('用户移动目标后解除原座椅关系并保存历史，完整导出及新会话导入可恢复')
   anchored=copy.deepcopy(p);anchored['id']='anchored-browser';anchored['furniture']=[copy.deepcopy(p['furniture'][0])]
   anchored['furniture'][0].update(cx=2500,cy=1000)
   anchored['furniture'][0]['placement']={'mode':'wall','roomId':'R1','edgeIndex':0,'side':'back','gap':0,'maxAdjust':120,'evidence':'夹具北墙接触'}
   anchored['walls']=[[0,-200,20000,0,'u']]
   page.evaluate('p=>Floorplan.loadPlan(p)',anchored);saved=page.evaluate('Floorplan.exportPlan()')
   for kind in ['gap','missing-wall']:
    bad=copy.deepcopy(anchored)
    if kind=='gap':bad['furniture'][0]['cy']+=100
    else:bad['walls']=[]
    message=page.evaluate("p=>{try{Floorplan.loadPlan(p);return ''}catch(e){return e.message}}",bad)
    assert '接触' in message and page.evaluate('Floorplan.exportPlan()')==saved,(kind,message)
   checks.append('浏览器拒绝未校正的贴墙间隙与虚构支撑，不能绕过构建器导入悬空家具')
   # 全高/剖切只改变墙体显示，不得缩短家具或改变保存的数据。
   height_plan=copy.deepcopy(p);height_plan['id']='height-cutaway-review'
   height_plan['walls']=[[0,-200,20000,0,'u']]
   page.evaluate('p=>Floorplan.loadPlan(p)',height_plan)
   page.locator('[data-view="3d"]').click();page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')")
   page.locator('[data-cut="full"]').click();full=page.evaluate('View3D.diagnostics()')
   saved=page.evaluate('Floorplan.exportPlan()')
   assert abs(full['actualWallTopMm']-2800)<1 and '2.80m' in page.locator('#hint3d').inner_text()
   assert all(f['bottomMm']+f['heightMm']<=2802 for f in full['furnitureBounds'])
   page.locator('[data-cut="1.2"]').click();cut=page.evaluate('View3D.diagnostics()')
   assert abs(cut['actualWallTopMm']-1200)<1 and '1.20m' in page.locator('#hint3d').inner_text()
   assert '家具保持原高' in page.locator('#hint3d').inner_text()
   assert full['furnitureBounds']==cut['furnitureBounds'] and page.evaluate('Floorplan.exportPlan()')==saved
   wardrobe=next(f for f in height_plan['furniture'] if f['type']=='wardrobe');wardrobe['height']=3100
   page.evaluate('p=>Floorplan.loadPlan(p)',height_plan)
   actual=page.evaluate('View3D.diagnostics().furnitureBounds')
   assert next(f for f in actual if f['id']==wardrobe['id'])['heightMm']>3000
   assert any(f['bottomMm']+f['heightMm']>2802 for f in actual)
   page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
   hint=page.locator('#hint3d').bounding_box();stage=page.locator('#stage').bounding_box()
   assert hint['x']>=stage['x'] and hint['x']+hint['width']<=stage['x']+stage['width']+1
   checks.append('实际柜高/全高墙/1.2m剖切墙分别复核；超高网格可检出，切换不改家具或数据，手机提示不溢出')
   assert not errors,errors;context.close();browser.close()
  report={'ok':True,'checks':checks,'pageErrors':errors,'source':'self-authored fixtures; real-image checks saved separately'};(out/'semantic-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))
 finally:server.shutdown()

if __name__=='__main__':run()
