"""替换目标的纯CPU平面筛查；真实GLB三角投影，不等同3D/人体工学验证。

仅检查target_ids。自定义家具使用完整三角面投影并集，默认家具用名义矩形。
不按高度忽略叠放；家具叠放必须在allowed_overlaps逐对写明reason。
512像素局部栅格有量化误差；边界至少容忍2mm/一个像素，轻微接触单列告警。
"""
import math
import json
import struct

from asset_validation import MAX_GLB_BYTES, decode_data_url, validate_asset
from reconstruct import footprint

RESOLUTION = 512
TOLERANCE_MM = 2.0


def _bbox(polygons):
    import numpy as np
    points = np.concatenate(polygons)
    return [float(points[:, 0].min()), float(points[:, 1].min()),
            float(points[:, 0].max()), float(points[:, 1].max())]


def _intersect(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def _near(a, b):
    r = _intersect(a, b)
    return r[2] > r[0] and r[3] > r[1]


def _nominal_bounds(f):
    values = [f[k] for k in ('cx', 'cy', 'w', 'd', 'rot')]
    if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
            for v in values) or f['w'] <= 0 or f['d'] <= 0):
        raise ValueError('家具中心、旋转与正宽深必须为有限数值')
    p = footprint(f)
    # 协议允许规范化边界有0.002误差，宽筛时不漏掉这些实际顶点。
    margin = max(f['w'], f['d']) * .0021 if f.get('assetId') else 0
    return [min(v[0] for v in p) - margin, min(v[1] for v in p) - margin,
            max(v[0] for v in p) + margin, max(v[1] for v in p) + margin]


def _asset_triangles(asset):
    """协议校验后直接读取静态GLB位置/索引；不依赖纹理颜色或PNG轮廓。"""
    import numpy as np
    validate_asset(asset)
    raw = decode_data_url(asset['dataUrl'], 'model/gltf-binary', MAX_GLB_BYTES)
    length = struct.unpack_from('<I', raw, 12)[0]
    document = json.loads(raw[20:20 + length])
    binary = raw[28 + length:]
    def accessor(index):
        a = document['accessors'][index]
        v = document['bufferViews'][a['bufferView']]
        dtype = {5121: 'u1', 5123: '<u2', 5125: '<u4', 5126: '<f4'}[a['componentType']]
        dimension = {'SCALAR': 1, 'VEC3': 3}[a['type']]
        size = np.dtype(dtype).itemsize
        return np.ndarray((a['count'], dimension), dtype=dtype, buffer=binary,
                          offset=v.get('byteOffset', 0) + a.get('byteOffset', 0),
                          strides=(v.get('byteStride', dimension * size), size))
    parts = []
    for mesh in document['meshes']:
        for primitive in mesh['primitives']:
            vertices = accessor(primitive['attributes']['POSITION'])[:, [0, 2]].astype(float)
            indices = accessor(primitive['indices']).reshape(-1, 3) if 'indices' in primitive else np.arange(len(vertices)).reshape(-1, 3)
            parts.append(vertices[indices])
    return np.concatenate(parts)


def _shape(f, assets, cache):
    import numpy as np
    _nominal_bounds(f)
    aid = f.get('assetId')
    if not aid:
        polygons = [np.asarray(footprint(f), dtype=float)]
        return {'polygons': polygons, 'bounds': _bbox(polygons), 'source': 'nominal-rectangle'}
    if aid not in assets:
        raise ValueError('家具引用了不存在的资产: ' + str(aid))
    if assets[aid].get('id') != aid:
        raise ValueError('资产表key与资产id不一致')
    if aid not in cache:
        cache[aid] = _asset_triangles(assets[aid])
    triangles = cache[aid]
    x, z = triangles[:, :, 0] * f['w'], triangles[:, :, 1] * f['d']
    angle = math.radians(f['rot'])
    c, s = math.cos(angle), math.sin(angle)
    world = np.stack([c * x - s * z + f['cx'], s * x + c * z + f['cy']], axis=2)
    # 竖直面投影的零面积线不当作正面积占地；封闭家具的顶/底面填充内部。
    area2 = ((world[:, 1, 0] - world[:, 0, 0]) * (world[:, 2, 1] - world[:, 0, 1])
             - (world[:, 1, 1] - world[:, 0, 1]) * (world[:, 2, 0] - world[:, 0, 0]))
    polygons = list(world[abs(area2) > 1e-8])
    if not polygons:
        raise ValueError('资产没有正面积三角投影，不能可靠筛查')
    return {'polygons': polygons, 'bounds': _bbox(polygons), 'source': 'embedded-glb-triangle-union'}


def _rectangle(rect):
    import numpy as np
    x0, y0, x1, y1 = rect[:4]
    return {'polygons': [np.asarray([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)],
            'bounds': [x0, y0, x1, y1], 'source': 'rectangle'}


def _grid(bounds):
    x0, y0, x1, y1 = bounds
    span = max(x1 - x0, y1 - y0)
    cell = max(span / (RESOLUTION - 8), .25)
    radius = max(1, int(math.ceil(TOLERANCE_MM / cell)))
    pad = (radius + 2) * cell
    region = [x0 - pad, y0 - pad, x1 + pad, y1 + pad]
    size = (max(1, int(math.ceil((region[2] - region[0]) / cell))),
            max(1, int(math.ceil((region[3] - region[1]) / cell))))
    return region, size, cell, radius


def _raster(polygons, region, size, cell):
    from PIL import Image, ImageDraw
    image = Image.new('L', size, 0)
    draw = ImageDraw.Draw(image)
    for polygon in polygons:
        points = [((float(x) - region[0]) / cell, (float(y) - region[1]) / cell) for x, y in polygon]
        draw.polygon(points, fill=255)
    return image


def _overlap(a, b):
    import numpy as np
    from PIL import ImageFilter
    intersection = _intersect(a['bounds'], b['bounds'])
    width, height = intersection[2] - intersection[0], intersection[3] - intersection[1]
    if width <= 0 or height <= 0:
        return {'overlap': False, 'uncertain': False}
    if min(width, height) <= TOLERANCE_MM:
        return {'overlap': False, 'uncertain': True, 'aabbIntersectionMm': [width, height],
                'boundaryToleranceMm': TOLERANCE_MM}
    region, size, cell, radius = _grid(intersection)
    target = _raster(a['polygons'], region, size, cell)
    other = np.asarray(_raster(b['polygons'], region, size, cell)) > 0
    raw = (np.asarray(target) > 0) & other
    # 只侵蚀目标边界；不会因另一物体被目标完整包住而抹掉碰撞。
    core = (np.asarray(target.filter(ImageFilter.MinFilter(2 * radius + 1))) > 0) & other
    return {'overlap': bool(core.any()), 'uncertain': bool(raw.any() and not core.any()),
            'rawOverlapAreaMm2': float(raw.sum() * cell * cell),
            'coreOverlapAreaMm2': float(core.sum() * cell * cell),
            'mmPerPixel': cell, 'rasterDiagonalErrorMm': math.sqrt(2) * cell,
            'boundaryToleranceMm': radius * cell,
            'aabbIntersectionMm': [width, height]}


def _room_screen(shape, rooms):
    import numpy as np
    from PIL import ImageFilter
    region, size, cell, radius = _grid(shape['bounds'])
    target = _raster(shape['polygons'], region, size, cell)
    floor = np.asarray(_raster([r['poly'] for r in rooms], region, size, cell)) > 0
    raw = (np.asarray(target) > 0) & ~floor
    core = (np.asarray(target.filter(ImageFilter.MinFilter(2 * radius + 1))) > 0) & ~floor
    return {'outside': bool(core.any()), 'uncertain': bool(raw.any() and not core.any()),
            'rawOutsideAreaMm2': float(raw.sum() * cell * cell),
            'coreOutsideAreaMm2': float(core.sum() * cell * cell),
            'mmPerPixel': cell, 'rasterDiagonalErrorMm': math.sqrt(2) * cell,
            'boundaryToleranceMm': radius * cell}


def screen_replacements(plan, target_ids, allowed_overlaps=None):
    """返回目标的结构化平面筛查报告；不修改plan，不筛非目标之间原有问题。"""
    result = {'ok': False, 'errors': [], 'warnings': [], 'checks': [],
              'method': 'CPU actual GLB triangle-projection union / nominal rectangles; local 512px raster; no height bypass; approximate 2D screening, not 3D or ergonomics'}
    def error(fid, kind, other, detail):
        result['errors'].append({'itemId': fid, 'kind': kind, 'with': other, 'detail': detail})
    if not isinstance(target_ids, (list, tuple, set)) or not target_ids:
        error(None, 'invalid_target_ids', None, '需要非空目标ID集合')
        return result
    ids = list(target_ids)
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        error(None, 'invalid_target_ids', None, '目标ID必须为不重复字符串')
        return result
    furniture = plan.get('furniture', [])
    lookup = {f.get('id'): f for f in furniture}
    if len(lookup) != len(furniture):
        error(None, 'invalid_plan', None, '家具ID重复，不能安全定位目标')
        return result
    for fid in ids:
        if fid not in lookup:
            error(fid, 'unknown_target', None, '目标家具不存在')
    allowances = {} if allowed_overlaps is None else allowed_overlaps
    if not isinstance(allowances, dict):
        error(None, 'invalid_allowed_overlaps', None, '需要targetId到逐对reason的对象')
        return result
    for fid, entries in allowances.items():
        if fid not in ids or not isinstance(entries, dict):
            error(fid, 'invalid_allowed_overlaps', None, '允许项必须属于本次目标且逐对列出')
            continue
        for other, reason in entries.items():
            if other not in lookup or other == fid or not isinstance(reason, str) or not reason.strip():
                error(fid, 'invalid_allowed_overlaps', other, '只接受现有另一家具ID与非空reason，不能放行墙/房间/门')
    if result['errors']:
        return result
    shapes, asset_cache = {}, {}
    for fid in ids:
        try:
            shapes[fid] = _shape(lookup[fid], plan.get('furnitureAssets', {}), asset_cache)
        except (ValueError, KeyError, TypeError, struct.error) as exc:
            error(fid, 'invalid_geometry', None, str(exc))
    pairs = set()
    for fid in ids:
        if fid not in shapes:
            continue
        shape, f = shapes[fid], lookup[fid]
        summary = {'itemId': fid, 'kind': 'target_screen', 'footprintSource': shape['source'],
                   'boundsMm': shape['bounds'], 'wallsChecked': len(plan.get('walls', [])),
                   'doorsChecked': len(plan.get('doors', [])), 'slidesChecked': len(plan.get('slides', [])),
                   'neighborPairsRasterized': 0}
        room_id = f.get('placement', {}).get('roomId') or f.get('roomId')
        rooms = [r for r in plan.get('rooms', []) if r['id'] == room_id] if room_id else plan.get('rooms', [])
        if not rooms:
            error(fid, 'outside_room', room_id, '没有可用房间范围或指定房间不存在')
        else:
            room_check = _room_screen(shape, rooms)
            summary['roomScope'] = room_id or 'all-room-union'
            summary['roomRaster'] = room_check
            if room_check['outside']:
                error(fid, 'outside_room', room_id or 'all-room-union', room_check)
            elif room_check['uncertain']:
                result['warnings'].append({'itemId': fid, 'kind': 'room_boundary_tolerance', 'detail': room_check})
        obstacles = [('wall_collision', 'wall:%d' % i, w) for i, w in enumerate(plan.get('walls', []))]
        obstacles += [('door_overlap', 'door:%d' % i, d['rect']) for i, d in enumerate(plan.get('doors', []))]
        obstacles += [('sliding_door_overlap', 'slide:%d' % i, d['rect']) for i, d in enumerate(plan.get('slides', []))]
        obstacles += [('window_overlap', 'window:%d' % i, w) for i, w in enumerate(plan.get('windows', []))]
        for kind, other, rect in obstacles:
            if not _near(shape['bounds'], rect[:4]):
                continue
            detail = _overlap(shape, _rectangle(rect))
            if detail['overlap']:
                if kind == 'window_overlap':
                    result['warnings'].append({'itemId': fid, 'kind': kind, 'with': other, 'detail': detail})
                else:
                    error(fid, kind, other, detail)
            elif detail['uncertain']:
                result['warnings'].append({'itemId': fid, 'kind': 'boundary_tolerance', 'with': other, 'detail': detail})
        for other, neighbor in lookup.items():
            if other == fid:
                continue
            pair = frozenset((fid, other))
            if pair in pairs:
                continue
            try:
                if not _near(shape['bounds'], _nominal_bounds(neighbor)):
                    continue
                if other not in shapes:
                    shapes[other] = _shape(neighbor, plan.get('furnitureAssets', {}), asset_cache)
            except (ValueError, KeyError, TypeError, struct.error) as exc:
                error(fid, 'neighbor_geometry_invalid', other, str(exc))
                continue
            pairs.add(pair)
            detail = _overlap(shape, shapes[other])
            summary['neighborPairsRasterized'] += 1
            reason = allowances.get(fid, {}).get(other) or allowances.get(other, {}).get(fid)
            if detail['overlap']:
                if reason:
                    result['checks'].append({'itemId': fid, 'kind': 'allowed_furniture_overlap',
                                             'with': other, 'reason': reason, 'detail': detail})
                else:
                    error(fid, 'furniture_overlap', other, detail)
            elif detail['uncertain']:
                result['warnings'].append({'itemId': fid, 'kind': 'boundary_tolerance', 'with': other, 'detail': detail})
        result['checks'].append(summary)
    result['ok'] = not result['errors']
    return result
