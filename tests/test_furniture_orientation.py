"""有证据的三轴刚体对齐：恢复已知姿态，不用剪切/镜像掩盖局部变形。"""
import base64
import io
import json
from pathlib import Path
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
from asset_validation import inspect_glb
from furniture_assets import normalized_meshes, prepare, render_top_view, validated_orientation


@unittest.skipUnless(OPTIONAL_DEPS, '三轴网格验收需可选numpy、trimesh、Pillow环境')
class OrientationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.image = self.root / 'self-authored-reference.png'
        Image.new('RGB', (20, 20), 'white').save(self.image)
        self.parts = []
        def box(extents, center, color):
            mesh = trimesh.creation.box(extents=extents)
            mesh.apply_translation(center)
            mesh.visual.vertex_colors = np.tile(color, (len(mesh.vertices), 1))
            self.parts.append(mesh)
        box([1.2, .12, 1.0], [0, .49, 0], [160, 160, 160, 255])
        box([1.2, .8, .11], [0, .88, -.445], [0, 0, 255, 255])
        # 红色单侧扶手同时检验左右、前后方向，不能用镜像凑相同包围盒。
        box([.16, .12, .75], [.64, .8, .05], [255, 0, 0, 255])
        for x in (-.48, .48):
            for z in (-.35, .35):
                box([.09, .43, .09], [x, .215, z], [35, 35, 35, 255])
        self.canonical = self.root / 'canonical.glb'
        trimesh.Scene(self.parts).export(self.canonical)
        rx = trimesh.transformations.rotation_matrix(np.deg2rad(17), [1, 0, 0])
        rz = trimesh.transformations.rotation_matrix(np.deg2rad(-12), [0, 0, 1])
        ry = trimesh.transformations.rotation_matrix(np.deg2rad(31), [0, 1, 0])
        self.pose = ry @ rz @ rx
        tilted = trimesh.Scene()
        for index, mesh in enumerate(self.parts):
            tilted.add_geometry(mesh.copy(), node_name='part_%d' % index, transform=self.pose)
        self.tilted = self.root / 'tilted-with-node-transforms.glb'
        tilted.export(self.tilted)
        self.orientation = {'rotation': self.pose[:3, :3].T.tolist(), 'evidence': '自制测试网格已知17° pitch、-12° roll、31° yaw；使用其逆旋转恢复。'}

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def rounded_cloud(meshes):
        points = np.round(np.concatenate([m.vertices for m in meshes]), 5)
        return points[np.lexsort((points[:, 2], points[:, 1], points[:, 0]))]

    def test_counter_rotation_restores_upright_geometry_and_footprint(self):
        baseline, base = normalized_meshes(self.canonical, 'y', '+z', 'linear')
        actual, alignment = normalized_meshes(self.tilted, 'z', '-y', 'linear', orientation_matrix=self.orientation)
        np.testing.assert_allclose(self.rounded_cloud(actual), self.rounded_cloud(baseline), atol=1e-5)
        np.testing.assert_allclose(alignment['sourceExtents'], base['sourceExtents'], atol=1e-6)
        bounds = np.concatenate([m.vertices for m in actual])
        np.testing.assert_allclose(bounds.min(axis=0), [-.5, 0, -.5], atol=1e-6)
        np.testing.assert_allclose(bounds.max(axis=0), [.5, 1, .5], atol=1e-6)
        self.assertEqual(alignment['orientationMode'], 'explicit_matrix')
        self.assertIsNone(alignment['sourceUpAxis'])
        self.assertIsNone(alignment['sourceFrontAxis'])
        self.assertEqual(alignment['orientationEvidence'], self.orientation['evidence'])
        # 恢复尺度后，边长与原网格相同；纯旋转不靠非均匀拉伸恢复姿态。
        for mesh, original in zip(actual, baseline):
            a = np.linalg.norm(np.diff(mesh.vertices * alignment['sourceExtents'], axis=0), axis=1)
            b = np.linalg.norm(np.diff(original.vertices * base['sourceExtents'], axis=0), axis=1)
            np.testing.assert_allclose(a, b, atol=1e-6)

    def test_top_view_keeps_front_back_and_asymmetric_side(self):
        baseline, _ = normalized_meshes(self.canonical, 'y', '+z', 'linear')
        corrected, _ = normalized_meshes(self.tilted, 'z', '-y', 'linear', orientation_matrix=self.orientation)
        a = np.array(Image.open(io.BytesIO(render_top_view(baseline, 96))))
        b = np.array(Image.open(io.BytesIO(render_top_view(corrected, 96))))
        ma, mb = a[..., 3] > 0, b[..., 3] > 0
        self.assertGreater((ma & mb).sum() / (ma | mb).sum(), .99)
        red = (b[..., 0] > 200) & (b[..., 1] < 30) & (b[..., 2] < 30) & mb
        blue = (b[..., 2] > 200) & (b[..., 0] < 30) & (b[..., 1] < 30) & mb
        self.assertGreater(np.nonzero(red)[1].mean(), 96 * .7)
        self.assertLess(np.nonzero(blue)[0].mean(), 96 * .25)

    def test_matrix_overrides_axis_options_and_cli_does_not_embed_path(self):
        orientation_path = self.root / 'reviewed-orientation.json'
        orientation_path.write_text(json.dumps(self.orientation, ensure_ascii=False))
        output = self.root / 'result/asset.json'
        command = [sys.executable, str(ROOT / 'skills/floorplan-image/scripts/furniture_assets.py'), 'prepare', '--mesh', str(self.tilted), '--source-image', str(self.image), '--asset-id', 'A_ROTATED', '--height-mm', '1280', '--up-axis', 'x', '--front-axis=+x', '--orientation-matrix', str(orientation_path), '--vertex-color-space', 'linear', '--resolution', '64', '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        asset = json.loads(output.read_text())
        self.assertEqual(asset['provenance']['orientation']['rotation'], self.orientation['rotation'])
        self.assertEqual(asset['provenance']['orientation']['evidence'], self.orientation['evidence'])
        self.assertNotIn(str(orientation_path), output.read_text())
        self.assertIsNone(asset['alignment']['sourceUpAxis'])
        self.assertIsNone(asset['alignment']['sourceFrontAxis'])
        glb = inspect_glb(base64.b64decode(asset['dataUrl'].split(',', 1)[1]))
        for node in glb['document']['nodes']:
            self.assertNotIn('translation', node)
            self.assertNotIn('rotation', node)
            self.assertNotIn('scale', node)
            if 'matrix' in node:
                np.testing.assert_allclose(np.array(node['matrix']).reshape(4, 4), np.eye(4), atol=1e-8)

    def test_cardinal_defaults_still_use_declared_axes(self):
        _, report = normalized_meshes(self.canonical, 'y', '+z', 'linear')
        self.assertEqual(report['orientationMode'], 'cardinal_axes')
        self.assertEqual(report['sourceUpAxis'], 'y')
        self.assertEqual(report['sourceFrontAxis'], '+z')
        np.testing.assert_allclose(report['sourceToCanonicalRotation'], np.eye(3), atol=1e-8)
        with self.assertRaisesRegex(ValueError, '正交'):
            normalized_meshes(self.canonical, 'x', '+x')

    def test_reflection_shear_and_nonfinite_cli_fail_without_output(self):
        invalid = [
            [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, .1, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 0, 0], [0, float('nan'), 0], [0, 0, 1]],
        ]
        for index, matrix in enumerate(invalid):
            with self.subTest(index=index):
                source = self.root / ('invalid-%d.json' % index)
                source.write_text(json.dumps({'rotation': matrix, 'evidence': 'invalid fixture'}))
                output = self.root / ('invalid-out-%d/asset.json' % index)
                command = [sys.executable, str(ROOT / 'skills/floorplan-image/scripts/furniture_assets.py'), 'prepare', '--mesh', str(self.tilted), '--source-image', str(self.image), '--asset-id', 'A_INVALID', '--height-mm', '900', '--orientation-matrix', str(source), '--output', str(output)]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertFalse(output.parent.exists())
        self.assertTrue(self.tilted.is_file())

    def test_evidence_and_numeric_matrix_are_required(self):
        invalid = [
            {'rotation': np.eye(3).tolist(), 'evidence': ' '},
            {'rotation': [[True, 0, 0], [0, 1, 0], [0, 0, 1]], 'evidence': 'test'},
            {'rotation': [['1', 0, 0], [0, 1, 0], [0, 0, 1]], 'evidence': 'test'},
            {'rotation': [[1, 0], [0, 1]], 'evidence': 'test'},
        ]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    validated_orientation(item)


if __name__ == '__main__':
    unittest.main()
