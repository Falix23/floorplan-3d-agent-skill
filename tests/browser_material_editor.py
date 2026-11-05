"""材质改色与整体3D操作的真实浏览器验收，仅使用自制小网格。"""
import base64
import functools
import http.server
import importlib.util
import io
import json
from pathlib import Path
import sys
import struct
import threading

from PIL import Image
from playwright.sync_api import sync_playwright
import trimesh
import numpy as np
from browser_runtime import configure_browser_runtime
from browser_furniture_assets import fixture_asset

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('plan_tools',ROOT/'skills/floorplan-image/scripts/plan_tools.py')
TOOLS=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(TOOLS)


def textured_asset():
    scene=trimesh.Scene()
    image=Image.new('RGBA',(4,4),(180,90,40,255))
    image.putpixel((0,0),(180,90,40,0))
    for name,x,color in [('Cushion',-.25,[255,255,255,255]),('Frame',.25,[40,80,120,255])]:
        mesh=trimesh.creation.box(extents=[.5,1,1]);mesh.apply_translation([x,.5,0])
        material=trimesh.visual.material.PBRMaterial(name=name,baseColorFactor=color,roughnessFactor=.7,metallicFactor=0,baseColorTexture=image if name=='Cushion' else None,alphaMode='MASK' if name=='Cushion' else 'OPAQUE',alphaCutoff=.5)
        uv=np.array([[0,0],[0,1],[1,0],[1,1]]*2,dtype=float)
        mesh.visual=trimesh.visual.TextureVisuals(uv=uv,material=material);scene.add_geometry(mesh,node_name=name,geom_name=name)
        if name=='Cushion':
            extra=mesh.copy();extra.apply_scale([1,.15,.4]);extra.apply_translation([0,.8,0]);extra.visual.material=material;scene.add_geometry(extra,node_name='CushionSecondPart',geom_name='CushionSecondPart')
    glb=scene.export(file_type='glb')
    top=io.BytesIO();Image.new('RGBA',(64,64),(180,90,40,255)).save(top,format='PNG')
    return {'id':'QATexture','format':'glb','dataUrl':'data:model/gltf-binary;base64,'+base64.b64encode(glb).decode(),'topViewDataUrl':'data:image/png;base64,'+base64.b64encode(top.getvalue()).decode(),'defaultHeightMm':900,'provenance':{'method':'self-authored-QA','license':'CC0','sourceImageSha256':'0'*64}}


def variant_asset():
    asset=fixture_asset('QASharedVariants')
    raw=base64.b64decode(asset['dataUrl'].split(',')[1]);n=struct.unpack_from('<I',raw,12)[0];tree=json.loads(raw[20:20+n]);binary=raw[28+n:]
    tree['materials'][0]['name']='SharedCloth'
    primitive=json.loads(json.dumps(tree['meshes'][0]['primitives'][0]));primitive['attributes'].pop('COLOR_0');tree['meshes'][0]['primitives'].append(primitive)
    doc=json.dumps(tree,separators=(',',':')).encode();doc+=b' '*(-len(doc)%4)
    glb=struct.pack('<III',0x46546c67,2,28+len(doc)+len(binary))+struct.pack('<II',len(doc),0x4e4f534a)+doc+struct.pack('<II',len(binary),0x004e4942)+binary
    asset['dataUrl']='data:model/gltf-binary;base64,'+base64.b64encode(glb).decode();return asset


def run():
    output=ROOT/'outputs/material-editor-browser-qa';output.mkdir(parents=True,exist_ok=True)
    plan=json.loads((ROOT/'examples/two-room.json').read_text());plan['id']='material-editor-qa';assets=[fixture_asset(),textured_asset(),variant_asset()]
    plan['furnitureAssets']={a['id']:a for a in assets}
    ids=[f['id'] for f in plan['furniture'][:3]]
    for f,aid in zip(plan['furniture'][:3],['QABox','QABox','QATexture']):f.update(assetId=aid,height=900)
    variant_id=plan['furniture'][4]['id'];plan['furniture'][4].update(assetId='QASharedVariants',height=900)
    TOOLS.build(plan,output/'index.html')
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    errors=[];checks=[]
    try:
      with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--ignore-certificate-errors'])
        context=browser.new_context(viewport={'width':1500,'height':1000},ignore_https_errors=True,accept_downloads=True);configure_browser_runtime(context)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));url=f'http://127.0.0.1:{server.server_port}/outputs/material-editor-browser-qa/index.html'
        page.goto(url);page.wait_for_function('!!window.View3D',timeout=90000)
        rows=page.evaluate('id=>View3D.materials(id)',ids[2]);assert len(rows)==2,rows
        original=page.evaluate('Floorplan.exportPlan()')
        page.evaluate("([id,recipe])=>Floorplan.applyFurnitureMaterial(id,recipe)",[ids[0],{'schemaVersion':1,'materials':[{'index':0,'color':'#246bd6','textureMode':'solid','roughness':.6,'evidence':'QA blue'}]}])
        blue=page.evaluate('Floorplan.exportPlan()');bf=next(f for f in blue['furniture'] if f['id']==ids[0]);assert bf['assetId']!='QABox';assert blue['furniture'][1]['assetId']=='QABox'
        assert blue['furnitureAssets']['QABox']==original['furnitureAssets']['QABox'];new_asset=blue['furnitureAssets'][bf['assetId']]
        (output/'blue.asset.json').write_text(json.dumps(new_asset))
        row=page.evaluate('id=>View3D.materials(id)',ids[0])[0];assert row['color']=='#246bd6',row
        image=Image.open(io.BytesIO(base64.b64decode(new_asset['topViewDataUrl'].split(',')[1]))).convert('RGBA');pixel=image.getpixel((192,192));assert abs(pixel[0]-36)<3 and abs(pixel[1]-107)<3 and abs(pixel[2]-214)<3,pixel
        checks.append('顶点色源模型显式去旧色：GLB材质与真实俯视PNG是相同目标蓝色；共享实例不变')
        page.keyboard.press('ControlOrMeta+z');assert page.evaluate('Floorplan.exportPlan()')==original
        page.keyboard.press('ControlOrMeta+Shift+z');assert page.evaluate('Floorplan.exportPlan()')==blue;checks.append('材质修改单步撤销与重做')
        asset_count=len(blue['furnitureAssets'])
        page.evaluate("([id,recipe])=>Floorplan.applyFurnitureMaterial(id,recipe)",[ids[0],{'schemaVersion':1,'materials':[{'index':0,'color':'#397c59','textureMode':'solid','roughness':.8,'evidence':'QA second color'}]}])
        repeated=page.evaluate('Floorplan.exportPlan()');assert len(repeated['furnitureAssets'])==asset_count;assert next(f['assetId'] for f in repeated['furniture'] if f['id']==ids[0])==bf['assetId']
        page.keyboard.press('ControlOrMeta+z');assert page.evaluate('Floorplan.exportPlan()')==blue
        checks.append('连续改色复用当前实例独占资产ID，当前方案不累积模型副本，撤销恢复上次字节')
        # PBR multi-material: target one material, leave frame and the shared original asset untouched.
        target=next(r for r in rows if r['name']=='Cushion')['index']
        page.evaluate("([id,recipe])=>Floorplan.applyFurnitureMaterial(id,recipe)",[ids[2],{'schemaVersion':1,'materials':[{'index':target,'color':'#9f241c','textureMode':'solid','roughness':.3,'evidence':'QA red'}]}])
        edited=page.evaluate('Floorplan.exportPlan()');ea=edited['furnitureAssets'][next(f['assetId'] for f in edited['furniture'] if f['id']==ids[2])]
        new_rows=page.evaluate('id=>View3D.materials(id)',ids[2]);assert len(new_rows)==2,new_rows;frame=next(r for r in new_rows if r['name']=='Frame');old_frame=next(r for r in rows if r['name']=='Frame');assert frame['color']==old_frame['color'];assert frame['roughness']==old_frame['roughness'];assert next(r for r in new_rows if r['name']=='Cushion')['hasColorTexture']
        (output/'pbr-red.asset.json').write_text(json.dumps(ea));checks.append('PBR仅修改所选材质，未选Frame与alpha镂空贴图保留')
        page.evaluate('id=>select({kind:"furn",id})',ids[2]);page.locator('#fMaterial').wait_for();cushion_row=next(r for r in new_rows if r['name']=='Cushion');page.locator('#fMaterial').select_option(str(cushion_row['index']));page.locator('#fMaterialColor').fill('#285b78');page.locator('#fMaterialApply').click();page.wait_for_function("async id=>(await View3D.materials(id)).find(m=>m.name==='Cushion').color==='#285b78'",arg=ids[2]);assert len(page.evaluate('id=>View3D.materials(id)',ids[2]))==2;edited=page.evaluate('Floorplan.exportPlan()')
        checks.append('真实材质面板显式应用改色；同源多mesh材质经多次导出后仍为同一分组')
        page.reload();page.wait_for_function('!!window.View3D',timeout=90000);assert page.evaluate('Floorplan.exportPlan()')==edited
        fresh=browser.new_context(viewport={'width':1500,'height':1000},ignore_https_errors=True);configure_browser_runtime(fresh);other=fresh.new_page();other.goto(url);other.wait_for_function('!!window.View3D',timeout=90000);other.evaluate('p=>Floorplan.loadPlan(p)',edited);assert other.evaluate('Floorplan.exportPlan()')==edited
        checks.append('完整JSON全新会话导入与刷新保留编辑后资产和2D图')
        assert len(page.evaluate('id=>View3D.materials(id)',variant_id))==1
        for color in ['#123456','#9a5b3c']:
            page.evaluate("([id,recipe])=>Floorplan.applyFurnitureMaterial(id,recipe)",[variant_id,{'schemaVersion':1,'materials':[{'name':'SharedCloth','color':color,'textureMode':'solid','evidence':'QA shared shader variants'}]}]);variants=page.evaluate('id=>View3D.materials(id)',variant_id);assert len(variants)==1 and variants[0]['color']==color,variants
        checks.append('同源材质有/无顶点色shader变体经重复导出后仍为一组，可继续按名称改色')
        before_reject=page.evaluate('Floorplan.exportPlan()')
        try:page.evaluate("([id,recipe])=>Floorplan.applyFurnitureMaterial(id,recipe)",[ids[2],{'schemaVersion':1,'materials':[{'index':999,'color':'#ff0000','textureMode':'solid','evidence':'invalid'}]}]);raise AssertionError('invalid material accepted')
        except Exception as e:assert '材质不存在' in str(e),str(e)
        assert page.evaluate('Floorplan.exportPlan()')==before_reject;checks.append('非法材质选择失败时原方案无变化')
        page.locator('[data-view="3d"]').click();page.wait_for_function("!document.querySelector('#stage').classList.contains('animating')",timeout=30000)
        page.evaluate('id=>select({kind:"furn",id})',ids[0]);page.locator('[data-ftransform="translate"]').click();page.wait_for_function('id=>View3D.editorState().attachedId===id',arg=ids[0]);assert page.evaluate('View3D.editorState().mode')=='translate'
        page.screenshot(path=str(output/'editor-gizmo.png'))
        # Real pointer drag: locate visible red X-axis pixels near the selected object, then drag it.
        # Diagnostic geometry (no synthetic objectChange) gives screen handle positions of the actual r160 control.
        handles=page.evaluate('View3D.transformHandles()')
        red=next(h for h in handles if h['axis']=='X');pose_before=page.evaluate('Floorplan.exportPlan()');p0=red['point'];page.mouse.move(p0['x'],p0['y']);page.mouse.down();page.mouse.move(p0['x']+55,p0['y'],steps=12);page.mouse.up();page.wait_for_timeout(100)
        after_drag=page.evaluate('Floorplan.exportPlan()');assert after_drag!=pose_before,handles
        assert next(f for f in after_drag['furniture'] if f['id']==ids[0])['height']==900
        page.keyboard.press('ControlOrMeta+z');assert page.evaluate('Floorplan.exportPlan()')==pose_before;checks.append('真实坐标轴指针拖动更新家具，单步撤销')
        for mode in ['rotate','scale']:
            page.locator(f'[data-ftransform="{mode}"]').click();page.wait_for_function('m=>View3D.editorState().mode===m',arg=mode)
            handles=page.evaluate('View3D.transformHandles()');axis='Y' if mode=='rotate' else 'X';handle=next(h for h in handles if h['axis']==axis);before=page.evaluate('Floorplan.exportPlan()');p0=handle['point'];page.mouse.move(p0['x'],p0['y']);page.mouse.down();page.mouse.move(p0['x']+(0 if mode=='rotate' else 55),p0['y']+(-55 if mode=='rotate' else 0),steps=15);page.mouse.up();page.wait_for_timeout(100)
            after=page.evaluate('Floorplan.exportPlan()');old=next(f for f in before['furniture'] if f['id']==ids[0]);new=next(f for f in after['furniture'] if f['id']==ids[0]);assert new['rot']!=old['rot'] if mode=='rotate' else new['w']!=old['w'],(mode,handles,new,old)
            page.keyboard.press('ControlOrMeta+z');assert page.evaluate('Floorplan.exportPlan()')==before
            checks.append('真实坐标轴指针'+('旋转' if mode=='rotate' else '缩放')+'更新共享状态并单步撤销')
        param_id=plan['furniture'][3]['id'];page.evaluate('id=>select({kind:"furn",id})',param_id);page.locator('#fH').fill('1500');page.locator('#fH').press('Tab');page.wait_for_function("id=>Math.abs(View3D.diagnostics().furnitureBounds.find(f=>f.id===id).heightMm-1500)<1",arg=param_id);assert abs(page.evaluate('id=>View3D.diagnostics().furnitureBounds.find(f=>f.id===id).bottomMm',param_id))<1
        checks.append('默认参数家具高度编辑同步实际网格并保持落地')
        page.screenshot(path=str(output/'editor-final.png'))
        assert not errors,errors
        (output/'report.json').write_text(json.dumps({'ok':True,'checks':checks,'errors':errors},ensure_ascii=False,indent=2)+'\n')
        browser.close()
    finally:server.shutdown()
    print(json.dumps({'ok':True,'checks':checks,'errors':errors},ensure_ascii=False))

if __name__=='__main__':run()
