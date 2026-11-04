"""真实三角投影筛查：自制L/圆/盒，不依赖案例图片或outputs。"""
import base64
import copy
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/floorplan-image/scripts'))
from furniture_placement import screen_replacements


def box(x0, z0, x1, z1):
    vertices = [(x, y, z) for y in (0, 1) for z in (z0, z1) for x in (x0, x1)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 7, 5), (4, 6, 7),
             (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return vertices, faces


def circle():
    vertices = [(0, 0, 0), (0, 1, 0)]
    for y in (0, 1):
        vertices += [(.5 * math.cos(i * math.pi / 32), y,
                      .5 * math.sin(i * math.pi / 32)) for i in range(64)]
    faces = []
    for i in range(64):
        j = (i + 1) % 64
        faces.extend([(0, 2 + i, 2 + j), (1, 66 + j, 66 + i),
                      (2 + i, 66 + i, 66 + j), (2 + i, 66 + j, 2 + j)])
    return vertices, faces


def asset(parts, aid='A_TEST'):
    vertices, faces = [], []
    for v, f in parts:
        offset = len(vertices)
        vertices.extend(v)
        faces.extend(tuple(i + offset for i in face) for face in f)
    positions = b''.join(struct.pack('<3f', *v) for v in vertices)
    indices = b''.join(struct.pack('<3H', *f) for f in faces)
    binary = positions + indices
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
           'nodes': [{'mesh': 0}], 'buffers': [{'byteLength': len(binary)}],
           'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(positions)},
                           {'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(indices)}],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(vertices), 'type': 'VEC3'},
                         {'bufferView': 1, 'componentType': 5123, 'count': len(faces) * 3, 'type': 'SCALAR'}],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}]}
    body = json.dumps(doc, separators=(',', ':')).encode()
    body += b' ' * (-len(body) % 4)
    binary += b'\0' * (-len(binary) % 4)
    raw = (struct.pack('<4sII', b'glTF', 2, 28 + len(body) + len(binary))
           + struct.pack('<II', len(body), 0x4E4F534A) + body
           + struct.pack('<II', len(binary), 0x004E4942) + binary)
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    # 故意用全不透明方PNG：必须依据GLB而不是PNG/包围框判断L空角与圆角。
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(b'\0\x80\x90\xa0\xff')) + chunk(b'IEND', b''))
    return {'id': aid, 'format': 'glb', 'dataUrl': 'data:model/gltf-binary;base64,' + base64.b64encode(raw).decode(),
            'topViewDataUrl': 'data:image/png;base64,' + base64.b64encode(png).decode(),
            'defaultHeightMm': 900, 'provenance': {'method': 'self-authored-test', 'license': 'CC0',
                                               'sourceImageSha256': '0' * 64}}


def item(fid='I1', cx=2000, cy=2000, w=1000, d=1000, aid=None, rot=0):
    value = {'id': fid, 'type': 'table', 'name': fid, 'cx': cx, 'cy': cy,
             'w': w, 'd': d, 'rot': rot, 'height': 900}
    if aid:
        value['assetId'] = aid
    return value


def plan(furniture, a=None):
    return {'rooms': [{'id': 'R1', 'poly': [[0, 0], [5000, 0], [5000, 5000], [0, 5000]]}],
            'walls': [], 'doors': [], 'slides': [], 'windows': [], 'furniture': furniture,
            'furnitureAssets': {a['id']: a} if a else {}}


@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('PIL'),
                     '需要requirements-furniture轻量依赖')
class PlacementTests(unittest.TestCase):
    def test_real_l_void_is_not_convex_hull_and_rotation_respected(self):
        a = asset([box(-.5, -.5, .5, 0), box(-.5, 0, 0, .5)])
        for rot, xy in [(0, (2500, 2500)), (90, (1500, 2500))]:
            p = plan([item(w=2000, d=2000, aid=a['id'], rot=rot),
                      item('I2', *xy, w=400, d=400)], a)
            before = copy.deepcopy(p)
            result = screen_replacements(p, {'I1'})
            self.assertTrue(result['ok'], result)
            self.assertEqual(p, before)
            self.assertEqual(result['checks'][-1]['footprintSource'], 'embedded-glb-triangle-union')
        p['furniture'][1]['cx'], p['furniture'][1]['cy'] = 2500, 1500
        self.assertIn('furniture_overlap', [e['kind'] for e in screen_replacements(p, ['I1'])['errors']])

    def test_round_bbox_corner_clear_but_real_overlap_rejected(self):
        a = asset([circle()])
        p = plan([item(aid=a['id']), item('I2', 2470, 2470, 100, 100)], a)
        self.assertTrue(screen_replacements(p, ['I1'])['ok'])
        p['furniture'][1].update(cx=2300, cy=2300)
        result = screen_replacements(p, ['I1'])
        self.assertEqual([e['kind'] for e in result['errors']], ['furniture_overlap'])
        self.assertGreater(result['errors'][0]['detail']['coreOverlapAreaMm2'], 100)
        self.assertGreater(result['errors'][0]['detail']['mmPerPixel'], 0)

    def test_room_range_uses_actual_round_not_bbox(self):
        a = asset([circle()])
        p = plan([item(cx=0, cy=0, aid=a['id'])], a)
        p['rooms'][0]['poly'] = [[-700, -700], [700, -700], [700, 50], [50, 700], [-700, 700]]
        self.assertTrue(screen_replacements(p, ['I1'])['ok'])
        del p['furniture'][0]['assetId']
        self.assertIn('outside_room', [e['kind'] for e in screen_replacements(p, ['I1'])['errors']])

    def test_boundary_contact_allowed_but_penetration_rejected(self):
        p = plan([item(cx=500, cy=1500)])
        p['walls'] = [[-200, 0, 0, 5000, 'e']]
        self.assertTrue(screen_replacements(p, ['I1'])['ok'])
        p['furniture'][0]['cx'] = 490
        kinds = [e['kind'] for e in screen_replacements(p, ['I1'])['errors']]
        self.assertIn('outside_room', kinds)
        self.assertIn('wall_collision', kinds)

    def test_furniture_allowance_explicit_and_height_does_not_bypass(self):
        p = plan([item(), item('I2')])
        p['furniture'][1]['height'] = 10000
        self.assertFalse(screen_replacements(p, ['I1'])['ok'])
        result = screen_replacements(p, {'I1', 'I2'}, {'I2': {'I1': '叠放示范，经人工核对'}})
        self.assertTrue(result['ok'], result)
        allowed = [c for c in result['checks'] if c['kind'] == 'allowed_furniture_overlap']
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]['reason'], '叠放示范，经人工核对')

    def test_allowance_never_bypasses_wall_door_slide_or_room(self):
        p = plan([item(cx=490), item('I2', cx=490)])
        p['walls'] = [[-200, 0, 0, 5000, 'e']]
        p['doors'] = [{'rect': [0, 1750, 100, 2250]}]
        p['slides'] = [{'rect': [900, 1750, 1000, 2250]}]
        kinds = [e['kind'] for e in screen_replacements(p, ['I1'], {'I1': {'I2': '桌椅叠放'}})['errors']]
        self.assertEqual(set(kinds), {'outside_room', 'wall_collision', 'door_overlap', 'sliding_door_overlap'})

    def test_window_warning_and_old_non_target_problems_retained(self):
        p = plan([item(), item('OLD1', cx=9000), item('OLD2', cx=9000)])
        p['windows'] = [[1900, 1900, 2100, 2100, 'n']]
        result = screen_replacements(p, ['I1'])
        self.assertTrue(result['ok'], result)
        self.assertIn('window_overlap', [w['kind'] for w in result['warnings']])

    def test_explicit_room_scope_and_room_union(self):
        p = plan([item(cx=2500)])
        p['rooms'] = [{'id': 'R1', 'poly': [[0, 0], [2500, 0], [2500, 5000], [0, 5000]]},
                      {'id': 'R2', 'poly': [[2500, 0], [5000, 0], [5000, 5000], [2500, 5000]]}]
        self.assertTrue(screen_replacements(p, ['I1'])['ok'])
        p['furniture'][0]['placement'] = {'roomId': 'R1'}
        self.assertFalse(screen_replacements(p, ['I1'])['ok'])

    def test_invalid_targets_assets_and_allowances_fail_closed(self):
        p = plan([item(), item('I2')])
        for targets, allow in [([], None), (['MISSING'], None), (['I1', 'I1'], None),
                               (['I1'], {'I1': {'wall:0': '不能'}}),
                               (['I1'], {'I1': {'I2': ''}}), (['I1'], {'I2': {'I1': '非目标'}})]:
            self.assertFalse(screen_replacements(p, targets, allow)['ok'])
        p['furniture'][0]['assetId'] = 'MISSING'
        result = screen_replacements(p, ['I1'])
        self.assertEqual(result['errors'][0]['kind'], 'invalid_geometry')


if __name__ == '__main__':
    unittest.main()
