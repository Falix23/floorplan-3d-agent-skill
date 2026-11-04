"""通用PBR保留入口：实际嵌入贴图，不改变几何/法线/UV或2D采样。"""
import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

try:
    import numpy as np
    import trimesh
    from PIL import Image
    OPTIONAL_DEPS = True
except ImportError:
    OPTIONAL_DEPS = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/floorplan-image/scripts'))
from furniture_assets import prepare


def unpack_asset(asset):
    data = base64.b64decode(asset['dataUrl'].split(',', 1)[1])
    length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + length]), data[28 + length:]


def attribute(document, binary, index):
    a = document['accessors'][index]
    v = document['bufferViews'][a['bufferView']]
    dtype = {5121: 'u1', 5123: '<u2', 5125: '<u4', 5126: '<f4'}[a['componentType']]
    dimensions = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[a['type']]
    size = np.dtype(dtype).itemsize
    return np.ndarray((a['count'], dimensions), dtype=dtype, buffer=binary,
                      offset=v.get('byteOffset', 0) + a.get('byteOffset', 0),
                      strides=(v.get('byteStride', dimensions * size), size)).copy()


@unittest.skipUnless(OPTIONAL_DEPS, 'PBR验收需轻量网格环境')
class FurniturePbrTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.image = self.root / 'reference.png'
        Image.new('RGB', (8, 8), 'white').save(self.image)
        pixels = np.zeros((32, 64, 3), dtype=np.uint8)
        pixels[:, :32] = [30, 70, 180]
        pixels[:, 32:] = [180, 40, 20]
        texture = Image.fromarray(pixels)
        normal = Image.new('RGB', (64, 32), (128, 128, 255))
        scene = trimesh.Scene()
        for index in range(2):
            mesh = trimesh.creation.box(extents=[.7, 1.2, .6])
            mesh.vertex_normals = mesh.vertex_normals.copy()
            material = trimesh.visual.material.PBRMaterial(
                name='authored-%d' % index, baseColorTexture=texture.copy(),
                normalTexture=normal.copy(), metallicFactor=.42, roughnessFactor=.37)
            mesh.visual = trimesh.visual.TextureVisuals(
                uv=np.tile([[0, 0], [1, 0], [0, 1], [1, 1]], (2, 1)), material=material)
            transform = trimesh.transformations.translation_matrix([index * 1.5, 0, 0])
            scene.add_geometry(mesh, node_name='part-%d' % index, transform=transform)
        self.mesh = self.root / 'authored-textured.glb'
        self.mesh.write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))

    def tearDown(self):
        self.directory.cleanup()

    def prepared(self, mode='vertex-color', size=32):
        return prepare(self.mesh, self.image, 'A_PBR_TEST', 1250, up_axis='y', front_axis='+z',
                       resolution=32, material_mode=mode, texture_max_size=size)

    def test_pbr_preserves_geometry_normals_uv_and_identical_top_view(self):
        before = hashlib.sha256(self.mesh.read_bytes()).hexdigest()
        vertex, _ = self.prepared()
        pbr, _ = self.prepared('preserve-pbr')
        a, a_bin = unpack_asset(vertex)
        b, b_bin = unpack_asset(pbr)
        for original, actual in zip(a['meshes'], b['meshes']):
            x, y = original['primitives'][0], actual['primitives'][0]
            for semantic in ('POSITION', 'NORMAL'):
                np.testing.assert_array_equal(attribute(a, a_bin, x['attributes'][semantic]),
                                              attribute(b, b_bin, y['attributes'][semantic]))
            np.testing.assert_array_equal(attribute(a, a_bin, x['indices']),
                                          attribute(b, b_bin, y['indices']))
        source = trimesh.load(self.mesh, force='scene', process=False)
        data = base64.b64decode(pbr['dataUrl'].split(',', 1)[1])
        actual = trimesh.load(io.BytesIO(data), file_type='glb', force='scene', process=False)
        for index, sample in enumerate(pbr['alignment']['colorSampling']):
            _, name = source.graph[sample['node']]
            np.testing.assert_array_equal(source.geometry[name].visual.uv,
                                          actual.geometry['geometry_%d' % index].visual.uv)
        self.assertEqual(vertex['topViewDataUrl'], pbr['topViewDataUrl'])
        self.assertEqual(hashlib.sha256(self.mesh.read_bytes()).hexdigest(), before)

    def test_pbr_images_are_embedded_resized_and_deduplicated(self):
        asset, report = self.prepared('preserve-pbr')
        doc, binary = unpack_asset(asset)
        self.assertEqual(len(doc['images']), 2)
        for image in doc['images']:
            self.assertNotIn('uri', image)
            view = doc['bufferViews'][image['bufferView']]
            png = Image.open(io.BytesIO(binary[view.get('byteOffset', 0):
                                              view.get('byteOffset', 0) + view['byteLength']]))
            self.assertEqual(png.size, (32, 16))
        for material in doc['materials']:
            pbr = material['pbrMetallicRoughness']
            self.assertEqual(pbr['metallicFactor'], .42)
            self.assertEqual(pbr['roughnessFactor'], .37)
            self.assertIn('baseColorTexture', pbr)
        processing = report['provenance']['materialProcessing']
        self.assertEqual(processing['mode'], 'preserve-pbr')
        self.assertEqual(len(processing['textureResizes']), 2)
        self.assertTrue(all(len(t['references']) == 2 for t in processing['textureResizes']))

    def test_default_mode_stays_vertex_color_with_neutral_material(self):
        asset, _ = self.prepared()
        doc, _ = unpack_asset(asset)
        self.assertNotIn('images', doc)
        self.assertEqual(asset['alignment']['materialMode'], 'vertex-color')
        self.assertEqual(len(doc['materials']), 1)
        self.assertEqual(doc['materials'][0]['pbrMetallicRoughness']['roughnessFactor'], .8)

    def test_pure_vertex_mode_keeps_explicit_srgb_conversion_when_preserving(self):
        mesh = trimesh.creation.box()
        mesh.visual.vertex_colors = np.tile([128, 64, 32, 255], (8, 1))
        path = self.root / 'srgb-vertices.glb'
        mesh.export(path)
        assets = []
        for mode in ('vertex-color', 'preserve-pbr'):
            asset, _ = prepare(path, self.image, 'A_SRGB', 1250, up_axis='y', front_axis='+z',
                               resolution=32, vertex_color_space='srgb', material_mode=mode)
            doc, binary = unpack_asset(asset)
            color_index = doc['meshes'][0]['primitives'][0]['attributes']['COLOR_0']
            np.testing.assert_array_equal(attribute(doc, binary, color_index),
                                          np.tile([55, 13, 4, 255], (8, 1)))
            assets.append(asset)
        self.assertEqual(assets[0]['topViewDataUrl'], assets[1]['topViewDataUrl'])

    def test_invalid_modes_and_sizes_fail_before_reading_input(self):
        for mode in ('unknown', None, True):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '材质模式'):
                prepare(None, None, 'A_BAD', 1250, material_mode=mode)
        for size in (None, True, 0, 31, 2049, 256.5, '256'):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, '贴图最大尺寸'):
                prepare(None, None, 'A_BAD', 1250, texture_max_size=size)

    def test_cli_uses_general_pbr_entry_and_reports_texture_sizes(self):
        output = self.root / 'result/asset.json'
        command = [sys.executable, str(ROOT / 'skills/floorplan-image/scripts/furniture_assets.py'),
                   'prepare', '--mesh', str(self.mesh), '--source-image', str(self.image),
                   '--asset-id', 'A_CLI_PBR', '--height-mm', '1250', '--up-axis', 'y',
                   '--front-axis=+z', '--resolution', '32', '--material-mode', 'preserve-pbr',
                   '--texture-max-size', '32', '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        asset = json.loads(output.read_text())
        self.assertEqual(asset['provenance']['materialProcessing']['textureMaxSize'], 32)
        self.assertEqual(len(unpack_asset(asset)[0]['images']), 2)


if __name__ == '__main__':
    unittest.main()
