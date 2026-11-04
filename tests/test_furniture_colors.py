"""真实PBR颜色来源、单色广播与纹理采样边界回归。"""
import base64
import hashlib
import io
import json
from pathlib import Path
import struct
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
import furniture_assets


@unittest.skipUnless(OPTIONAL_DEPS, '颜色验收需numpy、trimesh、Pillow轻量环境')
class FurnitureColorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def pbr_mesh(self, factor=None, texture=None, uv=None, colors=None):
        mesh = trimesh.creation.box(extents=[.7, 1.2, .7])
        if uv is None:
            uv = np.zeros((len(mesh.vertices), 2))
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=uv,
            material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=factor, baseColorTexture=texture, alphaMode='BLEND'),
        )
        if colors is not None:
            mesh.visual.vertex_attributes['color'] = np.asarray(colors, dtype=np.uint8)
        path = self.root / 'self-authored.glb'
        trimesh.Scene(mesh).export(path)
        return mesh, path

    def replace_color0_accessor(self, path, colors):
        """自制有效float/u16 COLOR_0，不借导出器的uint8强转构造夹具。"""
        data = path.read_bytes()
        length = struct.unpack_from('<I', data, 12)[0]
        document = json.loads(data[20:20 + length])
        binary_length = struct.unpack_from('<I', data, 20 + length)[0]
        binary = data[28 + length:28 + length + binary_length]
        encoded = colors.tobytes()
        document['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary),
                                        'byteLength': len(encoded)})
        accessor = {'bufferView': len(document['bufferViews']) - 1, 'count': len(colors),
                    'type': 'VEC%d' % colors.shape[1],
                    'componentType': 5126 if colors.dtype.kind == 'f' else 5123}
        if colors.dtype.kind != 'f':
            accessor['normalized'] = True
        document['accessors'].append(accessor)
        document['meshes'][0]['primitives'][0]['attributes']['COLOR_0'] = len(document['accessors']) - 1
        binary += encoded
        document['buffers'][0]['byteLength'] = len(binary)
        body = json.dumps(document, separators=(',', ':')).encode()
        body += b' ' * (-len(body) % 4)
        binary += b'\0' * (-len(binary) % 4)
        path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28 + len(body) + len(binary))
                         + struct.pack('<II', len(body), 0x4E4F534A) + body
                         + struct.pack('<II', len(binary), 0x004E4942) + binary)

    def test_pbr_material_does_not_discard_linear_color0(self):
        colors = np.array([[40, 100, 180, 255]] * 4 + [[180, 60, 40, 128]] * 4,
                          dtype=np.uint8)
        _, path = self.pbr_mesh(factor=[255, 255, 255, 255], colors=colors)
        actual, report = furniture_assets.normalized_meshes(path, 'y', '+z')
        np.testing.assert_array_equal(actual[0].visual.vertex_colors, colors)
        self.assertTrue(report['colorSampling'][0]['vertexColorMultiplierApplied'])

    def test_texture_factor_and_color0_multiply_once_in_linear_space_in_both_modes(self):
        pixel = np.array([128, 64, 32, 128], dtype=np.uint8)
        factor = np.array([128, 128, 255, 128], dtype=np.uint8)
        color0 = np.tile([64, 128, 255, 128], (8, 1)).astype(np.uint8)
        _, path = self.pbr_mesh(factor=factor,
                                texture=Image.new('RGBA', (4, 4), tuple(pixel)), colors=color0)
        expected = pixel.astype(float) / 255
        expected[:3] = np.where(expected[:3] <= .04045, expected[:3] / 12.92,
                                ((expected[:3] + .055) / 1.055) ** 2.4)
        expected = np.rint(expected * (factor / 255) * (color0[0] / 255) * 255).astype(np.uint8)
        meshes, _ = furniture_assets.normalized_meshes(path, 'y', '+z')
        np.testing.assert_array_equal(meshes[0].visual.vertex_colors, np.tile(expected, (8, 1)))
        image = self.root / 'reference.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        assets = []
        for mode in ('vertex-color', 'preserve-pbr'):
            asset, _ = furniture_assets.prepare(path, image, 'A_COLOR0', 1250,
                                                up_axis='y', front_axis='+z', resolution=32,
                                                material_mode=mode)
            decoded = trimesh.load(io.BytesIO(base64.b64decode(asset['dataUrl'].split(',', 1)[1])),
                                   file_type='glb', force='scene', process=False)
            visual = next(iter(decoded.geometry.values())).visual
            if mode == 'vertex-color':
                np.testing.assert_array_equal(visual.vertex_attributes['color'],
                                              np.tile(expected, (8, 1)))
            else:
                np.testing.assert_array_equal(visual.vertex_attributes['color'], color0)
                np.testing.assert_array_equal(visual.material.baseColorFactor, factor)
            assets.append(asset)
        self.assertEqual(assets[0]['topViewDataUrl'], assets[1]['topViewDataUrl'])

    def test_float_and_normalized_u16_color0_are_not_truncated_by_pbr_export(self):
        image = self.root / 'reference.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        for colors in (np.tile([.25, .5, 1.], (8, 1)).astype('<f4'),
                       np.tile([16384, 32768, 65535, 32768], (8, 1)).astype('<u2')):
            with self.subTest(dtype=colors.dtype):
                _, path = self.pbr_mesh(factor=[255, 255, 255, 255])
                self.replace_color0_accessor(path, colors)
                expected = colors.astype(float) / (65535 if colors.dtype.kind == 'u' else 1)
                if expected.shape[1] == 3:
                    expected = np.column_stack([expected, np.ones(8)])
                expected = np.rint(expected * 255).astype(np.uint8)
                for mode in ('vertex-color', 'preserve-pbr'):
                    asset, _ = furniture_assets.prepare(path, image, 'A_COLOR0_TYPED', 1250,
                                                        up_axis='y', front_axis='+z', resolution=32,
                                                        material_mode=mode)
                    decoded = trimesh.load(io.BytesIO(base64.b64decode(asset['dataUrl'].split(',', 1)[1])),
                                           file_type='glb', force='scene', process=False)
                    np.testing.assert_array_equal(next(iter(decoded.geometry.values())).visual.vertex_attributes['color'],
                                                  expected)

    def test_solid_pbr_factor_is_broadcast_without_srgb_double_conversion(self):
        mesh, path = self.pbr_mesh(factor=[92, 102, 107, 255])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        actual, report = furniture_assets.normalized_meshes(path, 'y', '+z')
        expected = np.tile([92, 102, 107, 255], (len(mesh.vertices), 1))
        np.testing.assert_array_equal(actual[0].visual.vertex_colors, expected)
        self.assertEqual(report['triangles'], len(mesh.faces))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_texture_colors_and_linear_factor_are_both_preserved(self):
        pixels = np.array([[[128, 64, 32, 255], [255, 0, 0, 255]],
                           [[0, 255, 0, 255], [0, 0, 255, 255]]], dtype=np.uint8)
        _, path = self.pbr_mesh(factor=[128, 255, 255, 255],
                                texture=Image.fromarray(pixels),
                                uv=np.tile([0, 1], (8, 1)))
        actual, _ = furniture_assets.normalized_meshes(path, 'y', '+z')
        rgb = pixels[0, 0, :3].astype(float) / 255
        linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
        expected = np.rint(linear * [128 / 255, 1, 1] * 255).astype(np.uint8)
        np.testing.assert_array_equal(actual[0].visual.vertex_colors[:, :3],
                                      np.tile(expected, (8, 1)))

    def test_unspecified_pbr_base_color_uses_gltf_default_white(self):
        _, path = self.pbr_mesh()
        actual, report = furniture_assets.normalized_meshes(path, 'y', '+z')
        np.testing.assert_array_equal(actual[0].visual.vertex_colors,
                                      np.tile([255, 255, 255, 255], (8, 1)))
        self.assertEqual(report['colorSampling'][0]['source'], 'pbr-default-white')

    def test_texture_nearest_wrap_does_not_index_past_image_edge(self):
        pixels = np.zeros((4, 4, 4), dtype=np.uint8)
        pixels[..., 3] = 255
        pixels[0, 0] = [255, 0, 0, 255]
        _, path = self.pbr_mesh(texture=Image.fromarray(pixels),
                                uv=np.tile([-.0001, 1.0001], (8, 1)))
        actual, _ = furniture_assets.normalized_meshes(path, 'y', '+z')
        np.testing.assert_array_equal(actual[0].visual.vertex_colors,
                                      np.tile([255, 0, 0, 255], (8, 1)))

    def test_glb_vertex_colors_remain_linear(self):
        mesh = trimesh.creation.box()
        mesh.visual.vertex_colors = np.tile([128, 64, 32, 255], (8, 1))
        path = self.root / 'linear-vertex-colors.glb'
        mesh.export(path)
        actual, _ = furniture_assets.normalized_meshes(path, 'y', '+z')
        np.testing.assert_array_equal(actual[0].visual.vertex_colors,
                                      np.tile([128, 64, 32, 255], (8, 1)))

    def test_malformed_color_arrays_are_rejected_instead_of_guessed(self):
        invalid = [None, [], [1, 2], [[1, 2, 3, 255]],
                   [[1, 2, 3, 255]] * 7, [256, 0, 0, 255],
                   [-1, 0, 0, 255], [float('nan'), 0, 0, 255],
                   ['92', '102', '107', '255'], [True, False, True, True]]
        for colors in invalid:
            with self.subTest(colors=colors):
                with self.assertRaisesRegex(ValueError, '颜色'):
                    furniture_assets.normalized_rgba(colors, 8)

    def test_full_prepare_keeps_solid_color_and_real_top_view(self):
        mesh, path = self.pbr_mesh(factor=[92, 102, 107, 255])
        image = self.root / 'self-authored-reference.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        asset, report = furniture_assets.prepare(path, image, 'A_PBR', 1250,
                                                up_axis='y', front_axis='+z', resolution=32)
        glb = base64.b64decode(asset['dataUrl'].split(',', 1)[1])
        # 直接核对Three.js会读取的COLOR_0，避免导入器对PBR+顶点色的抽象差异。
        json_length = struct.unpack_from('<I', glb, 12)[0]
        document = json.loads(glb[20:20 + json_length])
        binary = glb[28 + json_length:]
        primitive = document['meshes'][0]['primitives'][0]
        accessor = document['accessors'][primitive['attributes']['COLOR_0']]
        view = document['bufferViews'][accessor['bufferView']]
        self.assertEqual(accessor['componentType'], 5121)
        self.assertTrue(accessor['normalized'])
        colors = np.ndarray((accessor['count'], 4), dtype=np.uint8, buffer=binary,
                            offset=view.get('byteOffset', 0) + accessor.get('byteOffset', 0),
                            strides=(view.get('byteStride', 4), 1))
        np.testing.assert_array_equal(colors,
                                      np.tile([92, 102, 107, 255], (len(mesh.vertices), 1)))
        png = Image.open(io.BytesIO(base64.b64decode(asset['topViewDataUrl'].split(',', 1)[1])))
        self.assertEqual(png.mode, 'RGBA')
        self.assertGreater(np.count_nonzero(np.array(png)[..., 3]), 0)
        self.assertTrue(report['ok'])


if __name__ == '__main__':
    unittest.main()
