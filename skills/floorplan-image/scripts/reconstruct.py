"""从视觉 Agent 的像素观测恢复户型，显式贴墙约束与几何复核。"""
import argparse
import copy
import json
import math
import statistics
from pathlib import Path


def polygon_area(poly):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1])))/2


def footprint(f):
    c,s=math.cos(math.radians(f['rot'])),math.sin(math.radians(f['rot']))
    return [[f['cx']+c*x-s*y,f['cy']+s*x+c*y] for x,y in [(-f['w']/2,-f['d']/2),(f['w']/2,-f['d']/2),(f['w']/2,f['d']/2),(-f['w']/2,f['d']/2)]]


def intersection_depth(poly,rect):
    """OBB 与墙矩形的 SAT 穿透深度，接触为零。"""
    other=[[rect[0],rect[1]],[rect[2],rect[1]],[rect[2],rect[3]],[rect[0],rect[3]]]
    depth=float('inf')
    for a,b in zip(poly+other,poly[1:]+poly[:1]+other[1:]+other[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length<1e-8:continue
        n=(-dy/length,dx/length)
        p=[x*n[0]+y*n[1] for x,y in poly];q=[x*n[0]+y*n[1] for x,y in other]
        overlap=min(max(p),max(q))-max(min(p),min(q))
        if overlap<=0:return 0
        depth=min(depth,overlap)
    return depth


def edge_data(plan,f):
    a=f['placement'];r=next((r for r in plan['rooms'] if r['id']==a.get('roomId')),None)
    if r is None:raise ValueError(f['id']+' 贴墙房间不存在')
    i=a.get('edgeIndex')
    if not isinstance(i,int) or isinstance(i,bool) or not 0<=i<len(r['poly']):raise ValueError(f['id']+' 贴墙边索引无效')
    v,w=r['poly'][i],r['poly'][(i+1)%len(r['poly'])]
    dx,dy=w[0]-v[0],w[1]-v[1];length=math.hypot(dx,dy)
    if length<1 or abs(dx*dy)>1e-6:raise ValueError('贴墙边须为非零正交边')
    # 屏幕坐标系下顺时针的内侧法线。
    signed=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(r['poly'],r['poly'][1:]+r['poly'][:1]))
    if signed<=0:raise ValueError(r['id']+' 贴墙约束需要屏幕顺时针轮廓')
    t=(dx/length,dy/length);n=(-t[1],t[0])
    side=a.get('side','back')
    normals={'back':(0,-1),'front':(0,1),'left':(-1,0),'right':(1,0)}
    if side not in normals:raise ValueError('贴墙 side 无效')
    x,y=normals[side];c,s=math.cos(math.radians(f['rot'])),math.sin(math.radians(f['rot']))
    outward=(c*x-s*y,s*x+c*y)
    if outward[0]*n[0]+outward[1]*n[1]>-.999:
        raise ValueError(f['id']+' 朝向与贴墙面矛盾；请检查床头/柜背方向，不能用平移掩盖')
    half=(f['d'] if side in ('back','front') else f['w'])/2
    return v,t,n,length,half


def wall_covers(plan,v,t,n,start,end):
    """接触区须有真实墙面支撑；不能吸到门窗洞口或开放分区虚拟边。"""
    intervals=[]
    for w in plan['walls']:
        corners=[[w[0],w[1]],[w[2],w[1]],[w[2],w[3]],[w[0],w[3]]]
        normal=[(p[0]-v[0])*n[0]+(p[1]-v[1])*n[1] for p in corners]
        if abs(max(normal))>2:continue
        tang=[(p[0]-v[0])*t[0]+(p[1]-v[1])*t[1] for p in corners]
        intervals.append((min(tang),max(tang)))
    cursor=start
    for a,b in sorted(intervals):
        if a>cursor+2:break
        if b>=cursor:cursor=max(cursor,b)
        if cursor>=end-2:return True
    return False


def refine_plan(plan):
    p=copy.deepcopy(plan);adjustments=[]
    for f in p['furniture']:
        a=f.get('placement',{})
        if a.get('mode')!='wall':continue
        v,t,n,length,half=edge_data(p,f)
        gap=a.get('gap',0);limit=a.get('maxAdjust',120)
        if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in (gap,limit)) or gap<0 or limit<0:raise ValueError('贴墙 gap/maxAdjust 必须为非负有限数值')
        actual=(f['cx']-v[0])*n[0]+(f['cy']-v[1])*n[1]-half
        delta=gap-actual
        if abs(delta)>limit+1e-6:raise ValueError(f['id']+' 贴墙修正 %.1fmm 超过允许 %.1fmm，请复查观测'%(abs(delta),limit))
        before=[f['cx'],f['cy']];f['cx']+=n[0]*delta;f['cy']+=n[1]*delta
        tang=[(x-v[0])*t[0]+(y-v[1])*t[1] for x,y in footprint(f)]
        if min(tang)<-2 or max(tang)>length+2:raise ValueError(f['id']+' 家具接触区超出指定墙边')
        if not wall_covers(p,v,t,n,min(tang),max(tang)):raise ValueError(f['id']+' 贴墙区缺少实体墙，可能是门窗洞口或开放边界')
        if any(intersection_depth(footprint(f),w)>2 for w in p['walls']):raise ValueError(f['id']+' 贴墙后与其他墙体碰撞')
        adjustments.append({'id':f['id'],'before':before,'after':[f['cx'],f['cy']],'distanceMm':abs(delta),'targetGapMm':gap})
    return p,adjustments


def from_pixels(data):
    """观测框与图像先统一坐标，任何尺寸变化都留有证据。"""
    p={k:copy.deepcopy(data[k]) for k in ('id','title','height','uncertainties','furnitureAssets') if k in data}
    p.update(schemaVersion=1,units='mm',height=data.get('height',2800))
    calibration=data.get('calibration',{})
    anchors=[]
    if calibration.get('mmPerPixel') is not None:
        scale=calibration['mmPerPixel'];status=calibration.get('status','estimated');evidence=calibration.get('evidence','用户给定工作比例')
    else:
        for r in data['rooms']:
            if r.get('calibrationAreaM2'):
                net=polygon_area(r['polyPx'])-sum(polygon_area(h) for h in r.get('holesPx',[]))
                if net<=0:raise ValueError('面积标定的房间净面积须大于零')
                anchors.append((r['id'],r['calibrationAreaM2']*1e6/net))
        if not anchors:raise ValueError('需要 mmPerPixel、尺寸标注标定或至少一个可靠房间面积标注')
        scale=math.sqrt(statistics.median(v for _,v in anchors));status='estimated';evidence='根据房间面积标注与像素内轮廓估计，非实测长度'
    if not isinstance(scale,(int,float)) or not math.isfinite(scale) or scale<=0:raise ValueError('像素比例无效')
    ox,oy=data.get('originPx',[0,0])
    def pt(q):return [round((q[0]-ox)*scale,3),round((q[1]-oy)*scale,3)]
    def rect(q):return pt(q[:2])+pt(q[2:4])
    p['scale']={'status':status,'evidence':evidence,'mmPerPixel':scale,'areaAnchorCount':len(anchors)}
    p['reference']={'width':data['image']['width'],'height':data['image']['height'],'originPx':[ox,oy],'mmPerPixel':scale}
    p['rooms']=[]
    for r in data['rooms']:
        item={k:copy.deepcopy(v) for k,v in r.items() if k not in ('polyPx','atPx','holesPx','calibrationAreaM2')}
        item['poly']=list(map(pt,r['polyPx']))
        if 'holesPx' in r:item['holes']=[list(map(pt,h)) for h in r['holesPx']]
        if 'atPx'in r:item['at']=pt(r['atPx'])
        if 'calibrationAreaM2'in r:item['sourceAreaM2']=r['calibrationAreaM2']
        p['rooms'].append(item)
    p['walls']=[rect(w[:4])+[w[4]] for w in data['walls']]
    p['windows']=[rect(w) for w in data.get('windows',[])]
    p['doors']=[]
    for d in data.get('doors',[]):
        p['doors'].append({**{k:v for k,v in d.items() if k not in ('rectPx','hPx','lenPx')},'rect':rect(d['rectPx']),'h':pt(d['hPx']),'len':round(d['lenPx']*scale,3)})
    p['slides']=[{'rect':rect(d['rectPx']),'v':d['v']} for d in data.get('slides',[])]
    p['furniture']=[]
    for f in data['furniture']:
        x0,y0,x1,y1=f['bboxPx'];rotation=f.get('rot',0)%360
        if x1<=x0 or y1<=y0:raise ValueError('家具观测框须有正面积')
        if 'sizePx'in f:w,d=f['sizePx']
        elif rotation%90==0:w,d=(x1-x0,y1-y0) if rotation%180==0 else (y1-y0,x1-x0)
        else:raise ValueError('非直角家具需要局部 sizePx，不能从轴对齐框唯一反推')
        item={k:copy.deepcopy(v) for k,v in f.items() if k not in ('bboxPx','sizePx','placement')}
        item.update(cx=pt([(x0+x1)/2,(y0+y1)/2])[0],cy=pt([(x0+x1)/2,(y0+y1)/2])[1],w=round(w*scale,3),d=round(d*scale,3),rot=rotation)
        item['observation']={'bboxPx':f['bboxPx'],'rotation':rotation}
        if f.get('placement'):
            a=copy.deepcopy(f['placement'])
            if 'gapPx'in a:a['gap']=a.pop('gapPx')*scale
            if 'maxAdjustPx'in a:a['maxAdjust']=a.pop('maxAdjustPx')*scale
            item['placement']=a
        p['furniture'].append(item)
    return p


def audit_plan(plan):
    ref=plan.get('reference',{});scale=ref.get('mmPerPixel');ox,oy=ref.get('originPx',[0,0]);rows=[];collisions=[]
    for f in plan['furniture']:
        row={'id':f['id']};poly=footprint(f)
        for i,w in enumerate(plan['walls']):
            depth=intersection_depth(poly,w)
            if depth>2:collisions.append({'id':f['id'],'wallIndex':i,'penetrationMm':round(depth,3)})
        if f.get('placement',{}).get('mode')=='wall':
            v,t,n,length,half=edge_data(plan,f)
            gap=(f['cx']-v[0])*n[0]+(f['cy']-v[1])*n[1]-half
            row['wallGapMm']=round(gap,4);row['anchorErrorMm']=round(abs(gap-f['placement'].get('gap',0)),4)
        if scale and f.get('observation'):
            bb=f['observation']['bboxPx'];xs=[q[0]/scale+ox for q in poly];ys=[q[1]/scale+oy for q in poly];out=[min(xs),min(ys),max(xs),max(ys)]
            iw=max(0,min(bb[2],out[2])-max(bb[0],out[0]));ih=max(0,min(bb[3],out[3])-max(bb[1],out[1]));inter=iw*ih
            row['observedBBoxIoU']=round(inter/((bb[2]-bb[0])*(bb[3]-bb[1])+(out[2]-out[0])*(out[3]-out[1])-inter),5)
            row['centerErrorPx']=round(math.hypot((out[0]+out[2]-bb[0]-bb[2])/2,(out[1]+out[3]-bb[1]-bb[3])/2),4)
        rows.append(row)
    areas=[{'id':r['id'],'sourceAreaM2':r['sourceAreaM2'],'reconstructedAreaM2':round((polygon_area(r['poly'])-sum(polygon_area(h) for h in r.get('holes',[])))/1e6,3),'relativeError':round((polygon_area(r['poly'])-sum(polygon_area(h) for h in r.get('holes',[])))/1e6/r['sourceAreaM2']-1,4)} for r in plan['rooms'] if r.get('sourceAreaM2')]
    return {'furniture':rows,'wallCollisions':collisions,'roomAreas':areas,'note':'框一致性针对 Agent 记录的像素观测，不等同于真实图像识别准确率。'}


def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('command',choices=['from-pixels','refine','audit']);cli.add_argument('input');cli.add_argument('--output',required=True);cli.add_argument('--report')
    a=cli.parse_args();data=json.loads(Path(a.input).read_text())
    from plan_tools import validate
    if a.command=='audit':result=audit_plan(data)
    else:
        raw=from_pixels(data) if a.command=='from-pixels' else data
        validate(raw);result,adjustments=refine_plan(raw);validate(result)
        if a.report:Path(a.report).write_text(json.dumps({'adjustments':adjustments,**audit_plan(result)},ensure_ascii=False,indent=2))
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(target)

if __name__=='__main__':main()
