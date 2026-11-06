"""多层建筑协议、逐层构建与楼梯连接校验；不执行图像识别。"""
import copy
import json
import math
from pathlib import Path


def polygon_valid(poly, label):
    from plan_tools import point, cross, segments_touch
    if not isinstance(poly, list) or len(poly) < 3 or not all(point(p) for p in poly):
        raise ValueError(label + ' 轮廓无效')
    if len({tuple(p) for p in poly}) != len(poly):
        raise ValueError(label + ' 重复顶点')
    if abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(poly, poly[1:]+poly[:1]))) < 1:
        raise ValueError(label + ' 面积为零')
    for i in range(len(poly)):
        for j in range(i+1, len(poly)):
            if j == i+1 or (i == 0 and j == len(poly)-1):
                continue
            if segments_touch(poly[i], poly[(i+1) % len(poly)], poly[j], poly[(j+1) % len(poly)]):
                raise ValueError(label + ' 轮廓自交')


def rings_touch(a, b):
    from plan_tools import segments_touch
    return any(segments_touch(v, w, x, y) for v, w in zip(a, a[1:]+a[:1])
               for x, y in zip(b, b[1:]+b[:1]))


def validate_holes(room):
    from plan_tools import inside
    holes = room.get('holes', [])
    if not isinstance(holes, list):
        raise ValueError('holes 必须为多边形数组')
    for i, hole in enumerate(holes):
        polygon_valid(hole, room['id'] + ' 洞口')
        if not all(inside(p, room['poly']) for p in hole) or rings_touch(hole, room['poly']):
            raise ValueError(room['id'] + ' 洞口须严格位于房间内；接外边的挑空请使用凹多边形')
        for other in holes[:i]:
            if rings_touch(hole, other) or inside(hole[0], other) or inside(other[0], hole):
                raise ValueError(room['id'] + ' 洞口重叠')
    if room.get('at') and any(inside(room['at'], h) for h in holes):
        raise ValueError(room['id'] + ' 标签点不能位于洞口')


def usable(point, room):
    from plan_tools import inside
    return inside(point, room['poly']) and not any(inside(point, h) for h in room.get('holes', []))


def validate_building(building):
    from plan_tools import validate, finite, point, token, inside
    def need(ok, message):
        if not ok:
            raise ValueError(message)
    b = building
    need(b.get('schemaVersion') == 2 and b.get('units') == 'mm' and token(b.get('id')), '多层建筑需要 schemaVersion:2、units:mm 和有效 id')
    need(isinstance(b.get('title'), str), '多层建筑缺少 title')
    floors = b.get('floors')
    need(isinstance(floors, list) and 1 <= len(floors) <= 30, 'floors 需要1～30层')
    ids = set(); elevations = set(); warnings = []
    for f in floors:
        need(isinstance(f, dict) and token(f.get('id')) and f['id'] not in ids, '楼层 id 无效/重复')
        ids.add(f['id'])
        need(isinstance(f.get('name'), str) and f['name'].strip(), '楼层需要名称')
        need(finite(f.get('elevation')) and -100000 <= f['elevation'] <= 100000, '楼面标高无效')
        need(f['elevation'] not in elevations, '同标高请归并为一层；当前不支持重复标高楼层')
        elevations.add(f['elevation'])
        need(point(f.get('offset')), '每层需要平面对齐 offset:[x,y]')
        need(isinstance(f.get('alignment'), dict) and f['alignment'].get('status') in ('measured', 'estimated') and bool(f['alignment'].get('evidence')), '每层需要对齐依据 alignment.status/evidence')
        need(isinstance(f.get('vertical'), dict) and f['vertical'].get('status') in ('measured', 'estimated') and bool(f['vertical'].get('evidence')), '每层需要标高/层高依据 vertical.status/evidence')
        need(finite(f.get('slabThickness')) and 50 <= f['slabThickness'] <= 600, '楼板厚度需要50～600mm')
        need(isinstance(f.get('plan'), dict) and f['plan'].get('schemaVersion') == 1, 'floor.plan 必须是完整单层v1计划')
        warnings.extend(f['id'] + ': ' + w for w in validate(f['plan']))
        if f['alignment']['status'] != 'measured' or f['vertical']['status'] != 'measured':
            warnings.append(f['id'] + ': 平面对齐或竖向尺寸为估计，不能作为实测/施工数据')
    # 同一平面范围内上下层不可相交；错层结构先明确拆分，不自动猜测。
    ordered = sorted(floors, key=lambda f: f['elevation'])
    for lo, hi in zip(ordered, ordered[1:]):
        need(lo['elevation'] + lo['plan']['height'] <= hi['elevation'] - hi['slabThickness'] + 1,
             lo['id'] + '/' + hi['id'] + ' 标高、净高与楼板相互侵入；错层需另行建模')
    stairs = b.get('stairs', [])
    need(isinstance(stairs, list), 'stairs 必须是数组')
    by_id = {f['id']: f for f in floors}; ids = set()
    for s in stairs:
        need(isinstance(s, dict) and token(s.get('id')) and s['id'] not in ids, '楼梯 id 无效/重复')
        ids.add(s['id'])
        need(s.get('fromFloorId') in by_id and s.get('toFloorId') in by_id, '楼梯引用了不存在的楼层')
        lo, hi = by_id[s['fromFloorId']], by_id[s['toFloorId']]
        rise = hi['elevation'] - lo['elevation']
        need(rise > 0, '楼梯必须从低层指向高层')
        need(not any(lo['elevation'] < f['elevation'] < hi['elevation'] for f in floors), '跨越中间层的楼梯须分段连接相邻楼层')
        need(isinstance(s.get('name'), str), '楼梯需要名称')
        flights = s.get('flights')
        need(isinstance(flights, list) and 1 <= len(flights) <= 8, '楼梯需要1～8段直跑 flights')
        total = 0
        for flight in flights:
            need(isinstance(flight, dict) and point(flight.get('start')) and point(flight.get('end')), '楼梯起终点无效')
            dx, dy = [b-a for a, b in zip(flight['start'], flight['end'])]
            need((abs(dx) < 1e-6) != (abs(dy) < 1e-6), '当前楼梯仅支持非零正交直跑')
            need(finite(flight.get('width')) and 300 <= flight['width'] <= 5000, '楼梯宽度无效')
            need(finite(flight.get('rise')) and flight['rise'] > 0, '楼梯分段升高无效')
            need(isinstance(flight.get('steps'), int) and not isinstance(flight['steps'], bool) and 1 <= flight['steps'] <= 100, '楼梯级数无效')
            total += flight['rise']
            if not 100 <= flight['rise']/flight['steps'] <= 220:
                warnings.append(s['id'] + ': 踏步升高不在常见示意范围，须核对原图')
        need(abs(total-rise) <= 1, '楼梯总升高与上下层标高差不一致')
        landings = s.get('landings', [])
        need(isinstance(landings, list), '楼梯平台需要数组')
        for landing in landings:
            need(isinstance(landing, dict), '楼梯平台无效')
            polygon_valid(landing.get('poly'), '楼梯平台')
            need(finite(landing.get('elevation')) and 0 < landing['elevation'] < rise, '平台标高应为相对低层的中间标高')
        total = 0
        for i, flight in enumerate(flights[:-1]):
            total += flight['rise']; a, c = flight['end'], flights[i+1]['start']
            if math.dist(a, c) > 1:
                need(any(abs(l['elevation']-total) <= 1 and inside(a, l['poly']) and inside(c, l['poly']) for l in landings), '折返/转角梯段之间缺少连接平台')
        # 全宽梯段在接近上层处必须穿过真实空洞，不能只是2D画线。
        last = flights[-1]; dx, dy = [b-a for a, b in zip(last['start'], last['end'])]; length = math.hypot(dx, dy)
        nx, ny = -dy/length, dx/length
        for t in (.5, .8, .95):
            for side in (-.49, 0, .49):
                p = [last['start'][0]+dx*t+nx*last['width']*side-hi['offset'][0], last['start'][1]+dy*t+ny*last['width']*side-hi['offset'][1]]
                need(not any(usable(p, r) for r in hi['plan']['rooms']), s['id'] + ' 上层楼板封住梯段，请识别楼梯洞口')
    if len(floors) > 1 and not stairs:
        warnings.append('尚未建立楼梯连接；多层展示不等于楼层可达性验证')
    return warnings


def refine_building(building):
    from reconstruct import refine_plan
    result = copy.deepcopy(building); changes = []
    for f in result['floors']:
        f['plan'], rows = refine_plan(f['plan'])
        changes.extend(dict(row, floorId=f['id']) for row in rows)
    return result, changes


def assemble(manifest, base):
    """每层独立标定/复核后再组合，路径相对清单；原始输入不写入公共源码。"""
    import base64
    import mimetypes
    from reconstruct import from_pixels, refine_plan, audit_plan
    from plan_tools import validate
    b = copy.deepcopy(manifest); b['schemaVersion'] = 2; b['units'] = 'mm'
    reports = []
    for f in b['floors']:
        plan_file, observations_file = f.pop('planFile', None), f.pop('observationsFile', None)
        if (plan_file is not None) + (observations_file is not None) + ('plan' in f) != 1:
            raise ValueError('每层仅指定 plan、planFile 或 observationsFile 之一')
        if observations_file:
            f['plan'] = from_pixels(json.loads((base/observations_file).read_text()))
        elif plan_file:
            f['plan'] = json.loads((base/plan_file).read_text())
        validate(f['plan']); f['plan'], adjustments = refine_plan(f['plan'])
        image = f.pop('imageFile', None)
        if image:
            path = base/image; mime = mimetypes.guess_type(path.name)[0]
            if mime not in ('image/png', 'image/jpeg', 'image/webp') or not f['plan'].get('reference'):
                raise ValueError('每层参考图需要PNG/JPEG/WebP和本层像素标定')
            f['plan']['reference']['dataUrl'] = 'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()
        reports.append({'floorId': f['id'], 'adjustments': adjustments, **audit_plan(f['plan'])})
    warnings = validate_building(b)
    return b, {'warnings': warnings, 'floors': reports}


def main():
    import argparse
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('manifest'); cli.add_argument('--output', required=True); cli.add_argument('--html'); cli.add_argument('--report')
    args = cli.parse_args(); path = Path(args.manifest).resolve()
    try:
        b, report = assemble(json.loads(path.read_text()), path.parent)
        target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(b, ensure_ascii=False, indent=2)+'\n')
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        if args.html:
            from plan_tools import build
            build(b, args.html)
        print(json.dumps({'ok': True, 'output': args.output, 'warnings': report['warnings']}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as e:
        cli.exit(1, '多层构建失败: '+str(e)+'\n')


if __name__ == '__main__':
    main()
