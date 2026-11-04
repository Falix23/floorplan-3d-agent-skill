"""自定义家具的资产边界、位置持久化与无损失败验收。"""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/floorplan-image/scripts'))
from asset_validation import inspect_glb, validate_asset
from furniture_assets import attach
import plan_tools
from reconstruct import from_pixels


def fixture_glb(vertices=None, external=False, transform=False):
    vertices = vertices or [(-.5, 0, -.5), (.5, 0, -.5), (-.5, 0, .5), (.5, 1, .5)]
    positions = b''.join(struct.pack('<3f', *v) for v in vertices)
    indices = struct.pack('<12H', 0, 1, 2, 0, 3, 1, 1, 3, 2, 2, 3, 0)
    binary = positions + indices
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}], 'buffers': [{'byteLength': len(binary)}], 'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(positions)}, {'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(indices)}], 'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 4, 'type': 'VEC3'}, {'bufferView': 1, 'componentType': 5123, 'count': 12, 'type': 'SCALAR'}], 'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}]}
    if transform:
        doc['nodes'][0]['translation'] = [5, 0, 0]
    if external:
        doc['buffers'][0]['uri'] = 'https://example.invalid/private.bin'
    body = json.dumps(doc, separators=(',', ':')).encode()
    body += b' ' * (-len(body) % 4)
    binary += b'\0' * (-len(binary) % 4)
    return struct.pack('<4sII', b'glTF', 2, 12 + 16 + len(body) + len(binary)) + struct.pack('<II', len(body), 0x4E4F534A) + body + struct.pack('<II', len(binary), 0x004E4942) + binary


def fixture_png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\x80\x90\xa0\xff')) + chunk(b'IEND', b'')


def fixture_asset():
    return {'id': 'A_CHAIR', 'format': 'glb', 'dataUrl': 'data:model/gltf-binary;base64,' + base64.b64encode(fixture_glb()).decode(), 'topViewDataUrl': 'data:image/png;base64,' + base64.b64encode(fixture_png()).decode(), 'defaultHeightMm': 900, 'provenance': {'method': 'test-mesh', 'license': 'CC0-test-fixture', 'sourceImageSha256': '0' * 64}}


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / 'examples/two-room.json').read_text())
        self.asset = fixture_asset()

    def test_static_embedded_geometry_is_inspected(self):
        r = validate_asset(self.asset)
        self.assertEqual(r['triangles'], 4)
        self.assertEqual(r['bounds'], [[-.5, 0, -.5], [.5, 1, .5]])

    def test_replacement_preserves_placement_and_source_plan(self):
        before = copy.deepcopy(self.plan)
        result = attach(self.plan, self.asset, 'I1')
        self.assertEqual(self.plan, before)
        for key in ('cx', 'cy', 'w', 'd', 'rot', 'type', 'placement'):
            self.assertEqual(result['furniture'][0][key], before['furniture'][0][key])
        self.assertEqual(result['furniture'][0]['height'], 900)
        self.assertEqual(result['furnitureAssets']['A_CHAIR'], self.asset)
        self.assertEqual(plan_tools.validate(result), [])

    def test_existing_height_not_overwritten(self):
        self.plan['furniture'][0]['height'] = 650
        self.assertEqual(attach(self.plan, self.asset, 'I1')['furniture'][0]['height'], 650)

    def test_asset_id_collision_does_not_change_plan(self):
        result = attach(self.plan, self.asset, 'I1')
        before = copy.deepcopy(result)
        other = copy.deepcopy(self.asset)
        other['defaultHeightMm'] = 700
        with self.assertRaisesRegex(ValueError, 'id已被'):
            attach(result, other, 'I2')
        self.assertEqual(result, before)

    def test_missing_item_or_reference_rejected(self):
        with self.assertRaisesRegex(ValueError, '找不到'):
            attach(self.plan, self.asset, 'MISSING')
        self.plan['furniture'][0]['assetId'] = 'NOT_HERE'
        with self.assertRaisesRegex(ValueError, '不存在'):
            plan_tools.validate(self.plan)

    def test_truncated_and_external_glb_rejected(self):
        with self.assertRaisesRegex(ValueError, '长度'):
            inspect_glb(fixture_glb()[:-4])
        with self.assertRaisesRegex(ValueError, '内嵌'):
            inspect_glb(fixture_glb(external=True))

    def test_nonfinite_and_non_normalized_vertices_rejected(self):
        for vertices in [[(-.5, 0, -.5), (.5, 0, -.5), (-.5, 0, .5), (.5, float('nan'), .5)], [(-.5, 0, -.5), (.5, 0, -.5), (-.5, 0, .5), (.5, 2, .5)]]:
            bad = copy.deepcopy(self.asset)
            bad['dataUrl'] = 'data:model/gltf-binary;base64,' + base64.b64encode(fixture_glb(vertices)).decode()
            with self.assertRaises(ValueError):
                validate_asset(bad)

    def test_png_and_source_metadata_rejected_when_invalid(self):
        for key, value in [('topViewDataUrl', 'data:image/svg+xml;base64,PHN2Zy8+'), ('defaultHeightMm', float('nan')), ('id', '../../asset')]:
            bad = copy.deepcopy(self.asset)
            bad[key] = value
            with self.assertRaises(ValueError):
                validate_asset(bad)
        bad = copy.deepcopy(self.asset)
        bad['provenance']['sourceImageSha256'] = 'not-a-digest'
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            validate_asset(bad)

    def test_header_only_png_rejected_before_attach_or_build(self):
        bad = copy.deepcopy(self.asset)
        bad['topViewDataUrl'] = 'data:image/png;base64,' + base64.b64encode(fixture_png()[:33]).decode()
        before = copy.deepcopy(self.plan)
        with self.assertRaisesRegex(ValueError, 'IDAT/IEND'):
            attach(self.plan, bad, 'I1')
        self.assertEqual(self.plan, before)

    def test_unbaked_scene_transform_rejected(self):
        bad = copy.deepcopy(self.asset)
        bad['dataUrl'] = 'data:model/gltf-binary;base64,' + base64.b64encode(fixture_glb(transform=True)).decode()
        with self.assertRaisesRegex(ValueError, '烘焙节点变换'):
            validate_asset(bad)

    def test_single_file_build_retains_asset_and_escapes_metadata(self):
        self.asset['provenance']['note'] = '</script><script>bad()</script>'
        result = attach(self.plan, self.asset, 'I1')
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'index.html'
            plan_tools.build(result, out)
            payload = out.read_text().split('/* PLAN_DATA_START */', 1)[1].split('/* PLAN_DATA_END */', 1)[0]
            self.assertNotIn('</script>', payload)
            self.assertEqual(json.loads(payload)['furnitureAssets'], result['furnitureAssets'])

    def test_pixels_preserve_registered_asset(self):
        observation = {'id': 'P', 'title': '组合输入', 'image': {'width': 400, 'height': 500}, 'calibration': {'mmPerPixel': 10}, 'rooms': [{'id': 'R1', 'name': '房间', 'mat': 'wood', 'polyPx': [[0, 0], [400, 0], [400, 500], [0, 500]]}], 'walls': [], 'furniture': [{'id': 'I1', 'type': 'chair', 'name': '椅子', 'bboxPx': [10, 10, 50, 50], 'rot': 0, 'assetId': 'A_CHAIR'}], 'furnitureAssets': {'A_CHAIR': self.asset}}
        result = from_pixels(observation)
        self.assertEqual(result['furnitureAssets'], observation['furnitureAssets'])
        self.assertEqual(result['furniture'][0]['assetId'], 'A_CHAIR')
        self.assertIsNot(result['furnitureAssets'], observation['furnitureAssets'])


if __name__ == '__main__':
    unittest.main()
