"""保留作者法线，避免退化/未引用顶点在打包时被重算成NaN。"""
import base64
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
from furniture_assets import normalized_meshes, prepare


def glb_attribute(data, semantic):
    length = struct.unpack_from('<I', data, 12)[0]
    document = json.loads(data[20:20 + length])
    binary = data[28 + length:]
    primitive = document['meshes'][0]['primitives'][0]
    accessor = document['accessors'][primitive['attributes'][semantic]]
    view = document['bufferViews'][accessor['bufferView']]
    return np.ndarray((accessor['count'], 3), dtype='<f4', buffer=binary,
                      offset=view.get('byteOffset', 0) + accessor.get('byteOffset', 0),
                      strides=(view.get('byteStride', 12), 4)).copy()


@unittest.skipUnless(OPTIONAL_DEPS, '法线验收需轻量网格环境')
class FurnitureNormalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def authored_scene(self, zero_normals=False):
        box = trimesh.creation.box(extents=[.7, 1.2, .5])
        # 一个未引用的内部顶点：不能靠删除它来让重算法线不报错。
        vertices = np.vstack([box.vertices, [[0, 0, 0]]])
        mesh = trimesh.Trimesh(vertices=vertices, faces=box.faces, process=False)
        normal = np.array([1, 2, 3], dtype=float)
        normal /= np.linalg.norm(normal)
        mesh.vertex_normals = np.tile(normal if not zero_normals else [0, 0, 0], (9, 1))
        mesh.visual.vertex_colors = np.tile([128, 64, 32, 255], (9, 1))
        transform = trimesh.transformations.rotation_matrix(np.deg2rad(23), [0, 1, 0])
        transform[:3, :3] = transform[:3, :3] @ np.diag([2, 1, 3])
        transform[:3, 3] = [1, 2, 3]
        scene = trimesh.Scene()
        scene.add_geometry(mesh, transform=transform)
        path = self.root / 'self-authored-normals.glb'
        with np.errstate(invalid='ignore'):
            path.write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
        return path, normal, transform, vertices, box.faces

    def test_inverse_transpose_preserves_authored_normals_and_all_vertices(self):
        path, normal, transform, vertices, faces = self.authored_scene()
        meshes, report = normalized_meshes(path, 'y', '+z')
        actual = meshes[0]
        extent = np.asarray(report['sourceExtents'])
        full_linear = np.diag(1 / extent) @ transform[:3, :3]
        expected = np.linalg.inv(full_linear).T @ normal
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(actual.vertex_normals, np.tile(expected, (9, 1)), atol=1e-6)
        self.assertEqual(len(actual.vertices), len(vertices))
        np.testing.assert_array_equal(actual.faces, faces)
        world = trimesh.transformations.transform_points(vertices, transform)
        normalized = (world - [world[:, 0].min() + extent[0] / 2,
                               world[:, 1].min(), world[:, 2].min() + extent[2] / 2]) / extent
        np.testing.assert_allclose(actual.vertices, normalized, atol=1e-6)

    def test_prepare_exports_finite_unit_normals_including_unused_vertex(self):
        path, _, _, _, _ = self.authored_scene()
        image = self.root / 'reference.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        asset, _ = prepare(path, image, 'A_AUTHORED_NORMALS', 1250,
                           up_axis='y', front_axis='+z', resolution=32)
        data = base64.b64decode(asset['dataUrl'].split(',', 1)[1])
        normals = glb_attribute(data, 'NORMAL')
        self.assertEqual(len(normals), 9)
        self.assertTrue(np.isfinite(normals).all())
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-6)

    def test_invalid_authored_normals_are_rejected_without_deleting_geometry(self):
        path, _, _, _, _ = self.authored_scene(zero_normals=True)
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '法线'):
            normalized_meshes(path, 'y', '+z')
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
