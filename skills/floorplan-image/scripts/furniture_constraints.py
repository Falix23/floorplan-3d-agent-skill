"""源图家具语义约束：朝向与目标关系独立于包围盒；不推断未知方向。"""
import math

DIRECTED_TYPES = {'bed','sofa','armchair','wardrobe','cabinet','shoecab','bookshelf'}
SEATING_TYPES = {'sofa','armchair','chair','officechair'}


def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def front_direction(f):
    a=math.radians(f['rot'])
    return [-math.sin(a),math.cos(a)]


def angle_between(a,b):
    n=math.hypot(*a)*math.hypot(*b)
    if n<1e-8:raise ValueError('方向或目标向量不能为零')
    return math.degrees(math.acos(max(-1,min(1,sum(x*y for x,y in zip(a,b))/n))))


def audit_semantics(plan):
    policy=plan.get('furnitureReview',{});required=policy.get('required',False)
    targets=policy.get('requireFacingTargets',False);by_id={f['id']:f for f in plan['furniture']}
    errors=[];rows=[]
    for f in plan['furniture']:
        fid=f['id'];row={'id':fid,'type':f['type'],'frontDirection':front_direction(f)}
        orientation=f.get('orientationConstraint')
        if orientation is not None:
            if not isinstance(orientation,dict) or orientation.get('status') not in ('observed','inferred','unknown','released') or not orientation.get('evidence'):
                errors.append(fid+' 朝向约束需要状态与源图证据')
            elif orientation['status'] in ('observed','inferred'):
                front=orientation.get('front');tolerance=orientation.get('maxAngleDegrees')
                if (not isinstance(front,list) or len(front)!=2 or not all(finite(v) for v in front)
                        or math.hypot(*front)<1e-8 or not finite(tolerance) or not 0<=tolerance<=45
                        or not orientation.get('feature')):
                    errors.append(fid+' 观测朝向需front向量、特征与0～45度误差限')
                else:
                    error=angle_between(front_direction(f),front);row['sourceOrientationErrorDegrees']=error
                    if error>tolerance+1e-6:errors.append(fid+' 朝向与源图床头/靠背/柜门证据矛盾')
            elif orientation['status']=='unknown' and required:
                errors.append(fid+' 朝向待确认，不能作为已还原交付')
            row['orientationStatus']=orientation.get('status') if isinstance(orientation,dict) else 'invalid'
        elif required and f['type'] in DIRECTED_TYPES:
            errors.append(fid+' 缺少床头/靠背/柜门朝向约束')
        placement=f.get('placement',{})
        if required and f['type'] in DIRECTED_TYPES:
            if not isinstance(placement,dict) or placement.get('mode') not in ('wall','free') or not (placement.get('evidence') or placement.get('reason')):
                errors.append(fid+' 缺少接触墙面或保留自由摆放的证据')
            elif placement['mode']=='wall':
                if any(not finite(placement.get(k)) or placement[k]<0 for k in ('gap','maxAdjust')):
                    errors.append(fid+' 贴墙需显式gap/maxAdjust，不能使用未复核默认值')
        if placement.get('mode')=='wall':
            support=placement.get('support','solid-wall')
            if support not in ('solid-wall','window-plane'):errors.append(fid+' 接触支撑类型无效')
            if support=='window-plane' and (not placement.get('evidence') or placement.get('verticalVerified') is not False):
                errors.append(fid+' 临窗接触必须注明证据与竖向关系未验收')
            row['support']=support
        target=f.get('facingTarget')
        released=isinstance(orientation,dict) and orientation.get('status')=='released'
        if target is not None:
            if not isinstance(target,dict) or target.get('targetId') not in by_id or target['targetId']==fid or not target.get('evidence'):
                errors.append(fid+' 面向目标需要有效的本层对象与证据')
            elif not finite(target.get('maxAngleDegrees')) or not 0<=target['maxAngleDegrees']<90:
                errors.append(fid+' 面向目标角度限必须小于90度')
            else:
                t=by_id[target['targetId']];delta=[t['cx']-f['cx'],t['cy']-f['cy']]
                if math.hypot(*delta)<1e-8:errors.append(fid+' 面向目标与自身中心重合')
                else:
                    error=angle_between(front_direction(f),delta);row['targetFacingErrorDegrees']=error
                    if error>target['maxAngleDegrees']+1e-6:errors.append(fid+' 座面/正面背对指定电视、茶几或桌面')
        elif required and targets and f['type'] in SEATING_TYPES and not released:
            errors.append(fid+' 缺少座椅面向目标关系')
        row['placementMode']=placement.get('mode','unreviewed');rows.append(row)
    return {'ok':not errors,'errors':errors,'furniture':rows,
            'inferredDirections':[r['id'] for r in rows if r.get('orientationStatus')=='inferred'],
            'scope':'约束检查人工记录的源图证据；包围盒、程序通过不能证明证据识别正确'}


def validate_semantics(plan):
    policy=plan.get('furnitureReview',{})
    if not isinstance(policy,dict) or any(k in policy and not isinstance(policy[k],bool) for k in ('required','requireFacingTargets')):
        raise ValueError('furnitureReview开关必须为布尔值')
    result=audit_semantics(plan)
    if result['errors']:raise ValueError('; '.join(result['errors']))
    if policy.get('required'):
        # 延迟引用既有接触算法，避免另写一套墙面/洞口判断；原始观测仍允许限内待校正间隙。
        from reconstruct import edge_data,footprint,wall_covers
        for f in plan['furniture']:
            a=f.get('placement',{})
            if a.get('mode')!='wall':continue
            v,t,n,length,half=edge_data(plan,f)
            actual=(f['cx']-v[0])*n[0]+(f['cy']-v[1])*n[1]-half
            if abs(actual-a['gap'])>a['maxAdjust']+1e-6:raise ValueError(f['id']+' 接触间隙超出允许修正量')
            tang=[(x-v[0])*t[0]+(y-v[1])*t[1] for x,y in footprint(f)]
            if min(tang)<-2 or max(tang)>length+2:raise ValueError(f['id']+' 家具接触区超出指定墙边')
            if not wall_covers(plan,v,t,n,min(tang),max(tang),a.get('support','solid-wall')):
                raise ValueError(f['id']+' 接触区缺少实体墙/指定窗面')
    return result


def release_source_constraints(item, reason):
    """用户主动替换目标后保留原图证据；不把原图还原规则强加给新设计。"""
    import copy
    old=item.get('orientationConstraint')
    item['orientationConstraint']={'status':'released','previousSource':copy.deepcopy(old),'evidence':reason}
    placement=item.get('placement')
    if placement and placement.get('mode')=='wall':
        item['placement']={'mode':'free','previousWall':copy.deepcopy(placement),'reason':reason}
    elif not placement:
        item['placement']={'mode':'free','reason':reason}
    if item.get('facingTarget'):
        item['previousFacingTarget']=item.pop('facingTarget')
