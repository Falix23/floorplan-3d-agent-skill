"""照片配色按选中材质落入同一GLB/俯视图，不能染掉未选择的脚架。"""
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
    DEPS = True
except ImportError:
    DEPS = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/floorplan-image/scripts'))
from furniture_assets import prepare
from material_colors import material_inventory, validate_material_overrides


def unpack(asset):
    data = base64.b64decode(asset['dataUrl'].split(',', 1)[1])
    length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + length]), data[28 + length:]


def texture_image(doc, binary, texture_index):
    image = doc['images'][doc['textures'][texture_index]['source']]
    view = doc['bufferViews'][image['bufferView']]
    start = view.get('byteOffset', 0)
    return Image.open(io.BytesIO(binary[start:start + view['byteLength']])).convert('RGBA')


def linear(rgb):
    rgb = np.asarray(rgb, dtype=float) / 255
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


@unittest.skipUnless(DEPS, '材质配色验收需轻量网格依赖')
class MaterialOverridesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.image = self.root / 'reference.png'
        Image.new('RGB', (8, 8), '#285078').save(self.image)
        texture = Image.new('RGBA', (32, 32), (75, 135, 220, 255))
        normal = Image.new('RGB', (32, 32), (128, 128, 255))
        scene = trimesh.Scene()
        for i, name in enumerate(('upholstery', 'metal-legs')):
            mesh = trimesh.creation.box(extents=[.8, 1, .8])
            material = trimesh.visual.material.PBRMaterial(
                name=name, baseColorTexture=texture.copy(), normalTexture=normal.copy(),
                baseColorFactor=[1., 1., 1., 1.], metallicFactor=.9 if i else 0,
                roughnessFactor=.61 if i else .8)
            mesh.visual = trimesh.visual.TextureVisuals(
                uv=np.tile([[0, 0], [1, 0], [0, 1], [1, 1]], (2, 1)), material=material)
            scene.add_geometry(mesh, node_name='left' if not i else 'right',
                               transform=trimesh.transformations.translation_matrix([i * 1.2, 0, 0]))
        self.mesh = self.root / 'two-materials.glb'
        self.mesh.write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))

    def tearDown(self):
        self.directory.cleanup()

    def recipe(self, **change):
        return {'schemaVersion': 1, 'evidence': '参考照片左侧软包区域的代表色，色卡值为测试输入。',
                'materials': [dict(name='upholstery', color='#285078', textureMode='solid', **change)]}

    def prepared(self, recipe=None):
        return prepare(self.mesh, self.image, 'A_COLORS', 1000, up_axis='y', front_axis='+z',
                       resolution=64, material_mode='preserve-pbr', texture_max_size=32,
                       material_overrides=recipe)

    def test_solid_changes_only_selected_material_and_same_2d_color(self):
        original_hash = hashlib.sha256(self.mesh.read_bytes()).hexdigest()
        baseline, _ = self.prepared()
        asset, report = self.prepared(self.recipe(roughness=.24))
        doc, binary = unpack(asset)
        mats = {m['name']: m for m in doc['materials']}
        selected = mats['upholstery']['pbrMetallicRoughness']
        target = linear([40, 80, 120])
        np.testing.assert_allclose(selected['baseColorFactor'][:3], target, atol=1 / 255)
        self.assertNotIn('baseColorTexture', selected)
        self.assertEqual(selected['roughnessFactor'], .24)
        unselected = mats['metal-legs']['pbrMetallicRoughness']
        self.assertEqual(unselected['metallicFactor'], .9)
        self.assertEqual(unselected['roughnessFactor'], .61)
        np.testing.assert_array_equal(np.asarray(texture_image(doc, binary, unselected['baseColorTexture']['index']))[0, 0],
                                      [75, 135, 220, 255])
        # 两块物体的顶面都朝上；左块的俯视图需显示选中sRGB颜色，右块保持原颜色。
        top = np.asarray(Image.open(io.BytesIO(base64.b64decode(asset['topViewDataUrl'].split(',', 1)[1]))))
        np.testing.assert_allclose(top[32, 12, :3], [40, 80, 120], atol=4)
        before_top = np.asarray(Image.open(io.BytesIO(base64.b64decode(baseline['topViewDataUrl'].split(',', 1)[1]))))
        np.testing.assert_array_equal(top[:, 40:], before_top[:, 40:])
        self.assertEqual(hashlib.sha256(self.mesh.read_bytes()).hexdigest(), original_hash)
        self.assertEqual(report['provenance']['materialOverrides'][0]['name'], 'upholstery')
        self.assertEqual(report['alignment']['materialOverrides'], report['provenance']['materialOverrides'])

    def test_luminance_preserves_base_texture_alpha_and_other_maps(self):
        recipe = self.recipe()
        recipe['materials'][0]['textureMode'] = 'luminance'
        asset, report = self.prepared(recipe)
        doc, binary = unpack(asset)
        selected = next(m for m in doc['materials'] if m['name'] == 'upholstery')
        image = np.asarray(texture_image(doc, binary, selected['pbrMetallicRoughness']['baseColorTexture']['index']))
        self.assertTrue(np.all(image[..., 0] == image[..., 1]))
        self.assertTrue(np.all(image[..., 1] == image[..., 2]))
        self.assertTrue(np.all(image[..., 3] == 255))
        normal = np.asarray(texture_image(doc, binary, selected['normalTexture']['index']))
        np.testing.assert_array_equal(normal[0, 0], [128, 128, 255, 255])
        expected = linear([75, 135, 220]) @ [.2126, .7152, .0722]
        # 验证按线性亮度去色，而不是直接在sRGB通道求平均。
        actual = linear(image[0, 0, :3])[0]
        self.assertAlmostEqual(actual, expected, delta=.004)
        self.assertEqual(report['provenance']['materialOverrides'][0]['baseColorTexture'], 'linear-luminance-srgb-texture')

    def test_inventory_selects_by_current_source_index_and_prepared_asset_is_inspectable(self):
        inventory = material_inventory(path=self.mesh)
        self.assertEqual(inventory, material_inventory(path=self.mesh))
        self.assertEqual(len(inventory['materials']), 2)
        index = next(m['index'] for m in inventory['materials'] if m['name'] == 'upholstery')
        recipe = self.recipe()
        del recipe['materials'][0]['name']
        recipe['materials'][0]['index'] = index
        recipe['sourceMeshSha256'] = inventory['sourceMeshSha256']
        asset, _ = self.prepared(recipe)
        prepared = material_inventory(asset=asset)
        self.assertEqual(prepared['assetId'], 'A_COLORS')
        self.assertEqual({m['name'] for m in prepared['materials']}, {'upholstery', 'metal-legs'})
        recipe['sourceMeshSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, '哈希不符'):
            self.prepared(recipe)

    def test_colored_vertex_multiplier_is_neutralized_without_changing_alpha(self):
        scene = trimesh.load(self.mesh, force='scene', process=False)
        mesh = next(g for g in scene.geometry.values() if g.visual.material.name == 'upholstery')
        mesh.visual.vertex_attributes['color'] = np.tile([190, 35, 80, 128], (len(mesh.vertices), 1)).astype(np.uint8)
        scene.export(self.mesh)
        asset, _ = self.prepared(self.recipe())
        doc, binary = unpack(asset)
        # 重新读取GLB，确认COLOR_0不会继续把蓝色目标染成紫色。
        result = trimesh.load(io.BytesIO(base64.b64decode(asset['dataUrl'].split(',', 1)[1])),
                              file_type='glb', force='scene', process=False)
        actual = next(g for g in result.geometry.values() if g.visual.material.name == 'upholstery')
        rgba = np.asarray(actual.visual.vertex_attributes['color'])
        np.testing.assert_array_equal(rgba[:, :3], np.full((len(rgba), 3), 255, dtype=np.uint8))
        np.testing.assert_array_equal(rgba[:, 3], np.full(len(rgba), 128, dtype=np.uint8))

    def test_solid_keeps_texture_alpha_cutouts_and_uv(self):
        scene = trimesh.load(self.mesh, force='scene', process=False)
        mesh = next(g for g in scene.geometry.values() if g.visual.material.name == 'upholstery')
        pixels = np.asarray(mesh.visual.material.baseColorTexture.convert('RGBA')).copy()
        pixels[:16, :, 3] = 0
        pixels[16:24, :, 3] = 130
        mesh.visual.material.baseColorTexture = Image.fromarray(pixels)
        mesh.visual.material.alphaMode = 'MASK'
        mesh.visual.material.alphaCutoff = .35
        original_uv = mesh.visual.uv.copy()
        scene.export(self.mesh)
        asset, report = self.prepared(self.recipe())
        doc, binary = unpack(asset)
        material = next(m for m in doc['materials'] if m['name'] == 'upholstery')
        self.assertEqual(material['alphaMode'], 'MASK')
        self.assertAlmostEqual(material['alphaCutoff'], .35)
        texture = np.asarray(texture_image(doc, binary, material['pbrMetallicRoughness']['baseColorTexture']['index']))
        np.testing.assert_array_equal(texture[..., 3], pixels[..., 3])
        np.testing.assert_array_equal(texture[..., :3], np.full((32, 32, 3), 255, dtype=np.uint8))
        actual = trimesh.load(io.BytesIO(base64.b64decode(asset['dataUrl'].split(',', 1)[1])),
                              file_type='glb', force='scene', process=False)
        mesh = next(g for g in actual.geometry.values() if g.visual.material.name == 'upholstery')
        np.testing.assert_array_equal(mesh.visual.uv, original_uv)
        self.assertEqual(report['provenance']['materialOverrides'][0]['baseColorTexture'], 'alpha-only-rgb-white')

    def test_vertex_color_only_sources_can_use_pbr_mapping_without_double_gamma(self):
        mesh = trimesh.creation.box()
        mesh.visual.vertex_colors = np.tile([128, 64, 32, 255], (len(mesh.vertices), 1))
        path = self.root / 'plain-colors.ply'
        mesh.export(path)
        recipe = {'schemaVersion': 1, 'evidence': '自制色卡测试', 'materials': [
            {'index': 0, 'color': '#ffffff', 'textureMode': 'luminance', 'roughness': .32}]}
        asset, _ = prepare(path, self.image, 'A_PLY', 1000, up_axis='y', front_axis='+z',
                           resolution=32, material_mode='preserve-pbr', material_overrides=recipe)
        doc, _ = unpack(asset)
        self.assertAlmostEqual(doc['materials'][0]['pbrMetallicRoughness']['roughnessFactor'], .32)
        top = np.asarray(Image.open(io.BytesIO(base64.b64decode(asset['topViewDataUrl'].split(',', 1)[1]))))
        expected_linear = linear([128, 64, 32]) @ [.2126, .7152, .0722]
        actual_linear = linear(top[16, 16, :3])[0]
        self.assertAlmostEqual(actual_linear, expected_linear, delta=.008)

    def test_invalid_or_ambiguous_selectors_are_not_silent_successes(self):
        for update in ({'name': 'not-present'}, {'index': 999}, {'name': 'upholstery', 'index': 0}):
            recipe = self.recipe()
            recipe['materials'][0] = dict(color='#224466', textureMode='solid', **update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.prepared(recipe)
        scene = trimesh.load(self.mesh, force='scene', process=False)
        for geometry in scene.geometry.values():
            geometry.visual.material.name = 'same-name'
        scene.export(self.mesh)
        recipe = self.recipe()
        recipe['materials'][0]['name'] = 'same-name'
        with self.assertRaisesRegex(ValueError, '歧义'):
            self.prepared(recipe)

    def test_invalid_recipe_and_wrong_material_mode_are_rejected(self):
        patches = [{'color': 'blue'}, {'textureMode': 'tint'}, {'roughness': float('nan')}, {'index': True}, {'evidence': ''}]
        for patch in patches:
            recipe = self.recipe()
            recipe['materials'][0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_material_overrides(recipe)
        with self.assertRaisesRegex(ValueError, 'preserve-pbr'):
            prepare(self.mesh, self.image, 'A_WRONG', 1000, material_overrides=self.recipe())
        recipe = self.recipe()
        recipe['materials'].append(recipe['materials'][0].copy())
        with self.assertRaisesRegex(ValueError, '重复'):
            self.prepared(recipe)

    def test_cli_inventory_and_invalid_mapping_leave_no_outputs(self):
        script = ROOT / 'skills/floorplan-image/scripts/furniture_assets.py'
        result = subprocess.run([sys.executable, str(script), 'materials', '--mesh', str(self.mesh)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['materials']), 2)
        bad = self.recipe()
        bad['materials'][0]['name'] = 'missing'
        recipe_file = self.root / 'recipe.json'
        recipe_file.write_text(json.dumps(bad))
        output = self.root / 'must-not-exist/asset.json'
        result = subprocess.run([sys.executable, str(script), 'prepare', '--mesh', str(self.mesh),
                                 '--source-image', str(self.image), '--asset-id', 'A_FAIL', '--height-mm', '1000',
                                 '--material-mode', 'preserve-pbr', '--material-overrides', str(recipe_file),
                                 '--output', str(output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('无匹配', result.stderr)
        self.assertFalse(output.parent.exists())


if __name__ == '__main__':
    unittest.main()
