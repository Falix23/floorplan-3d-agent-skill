"""跨类别事务替换、失败保留原方案及可迁移产物；仅使用自制几何。"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/floorplan-image/scripts'
sys.path.insert(0, str(SCRIPTS))
from replace_furniture import read_plan, replace_plan, write_bundle
from test_furniture_assets import fixture_asset

MESH_DEPS = all(importlib.util.find_spec(name) for name in ('numpy', 'trimesh', 'PIL'))


def fixture_plan():
    return {'schemaVersion': 1, 'units': 'mm', 'id': 'ReplaceTest', 'title': '自制批量验收',
            'height': 2800, 'scale': {'status': 'measured', 'evidence': '自制测试坐标，非照片识别'},
            'rooms': [{'id': 'R1', 'name': '测试空间', 'mat': 'wood',
                       'poly': [[0, 0], [9000, 0], [9000, 6000], [0, 6000]]}],
            'walls': [], 'windows': [], 'doors': [], 'slides': [],
            'furniture': [
                {'id': 'I1', 'type': 'chair', 'name': '椅子', 'cx': 1500, 'cy': 1500,
                 'w': 600, 'd': 600, 'height': 900, 'rot': 0, 'color': '#444444'},
                {'id': 'I2', 'type': 'bed', 'name': '床', 'cx': 4500, 'cy': 1500,
                 'w': 1500, 'd': 2000, 'height': 800, 'rot': 0, 'color': '#cccccc'},
                {'id': 'I3', 'type': 'table', 'name': '桌子', 'cx': 4500, 'cy': 4500,
                 'w': 1200, 'd': 800, 'height': 750, 'rot': 0, 'color': '#eeeeee'}]}


@unittest.skipUnless(MESH_DEPS, '事务几何验收需要轻量家具依赖')
class ReplaceFurnitureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.plan = fixture_plan()
        self.original = copy.deepcopy(self.plan)
        self.asset = fixture_asset()
        self.asset['alignment'] = {'sourceExtents': [.6, .9, .6]}
        (self.directory / 'asset.json').write_text(json.dumps(self.asset))
        self.manifest = {'schemaVersion': 1, 'replacements': [
            {'itemId': 'I1', 'decision': 'replace', 'asset': 'asset.json', 'reason': '自制几何测试'},
            {'itemId': 'I2', 'decision': 'keep', 'reason': '无合适匹配，不改原床'}]}

    def tearDown(self):
        self.temp.cleanup()

    def apply(self):
        return replace_plan(self.plan, self.manifest, self.directory)

    def test_replace_and_keep_preserve_source_and_non_targets(self):
        result, report = self.apply()
        self.assertTrue(report['ok'], report)
        self.assertEqual(self.plan, self.original)
        self.assertEqual(result['furniture'][1:], self.original['furniture'][1:])
        self.assertEqual(result['rooms'], self.original['rooms'])
        self.assertEqual(result['furniture'][0]['assetId'], self.asset['id'])
        self.assertEqual(report['items'][0]['aspect']['status'], 'uniform')
        self.assertEqual(report['items'][1]['status'], 'kept')
        self.assertEqual(report['browserVerification']['status'], 'not_run')

    def test_photo_replacement_releases_original_constraints_without_changing_non_targets(self):
        self.plan['furnitureReview']={'required':True,'requireFacingTargets':True}
        self.plan['furniture'][0]['facingTarget']={'targetId':'I3','maxAngleDegrees':60,'evidence':'原图椅子朝右下桌面'}
        self.plan['furniture'][1].update(
            orientationConstraint={'status':'observed','front':[0,1],'feature':'headboard','maxAngleDegrees':5,'evidence':'原图床头在北'},
            placement={'mode':'free','evidence':'原图独立床位'})
        original=copy.deepcopy(self.plan)
        self.manifest['replacements'][0].update(set={'rot':90},placementEvidence='用户为新家具指定90度摆放')
        result,report=self.apply()
        self.assertTrue(report['ok'],report)
        self.assertEqual(self.plan,original)
        self.assertEqual(result['furniture'][1:],original['furniture'][1:])
        self.assertEqual(result['furniture'][0]['orientationConstraint']['status'],'released')
        self.assertEqual(result['furniture'][0]['previousFacingTarget']['targetId'],'I3')
        self.assertNotIn('facingTarget',result['furniture'][0])

    def test_second_invalid_asset_rejects_whole_batch_without_mutation(self):
        self.manifest['replacements'][1] = {
            'itemId': 'I2', 'decision': 'replace', 'asset': 'absent.json', 'reason': '测试缺失资产'}
        result, report = self.apply()
        self.assertIsNone(result)
        self.assertFalse(report['ok'])
        self.assertEqual(self.plan, self.original)
        self.assertNotIn('assetId', self.plan['furniture'][0])

    def test_dimension_and_placement_changes_need_evidence(self):
        row = self.manifest['replacements'][0]
        for fields, message in [({'w': 700}, 'dimensionsEvidence'), ({'rot': 45}, 'placementEvidence')]:
            row['set'] = fields
            result, report = self.apply()
            self.assertIsNone(result)
            self.assertIn(message, str(report['errors']))
        self.assertEqual(self.plan, self.original)

    def test_semantic_shape_change_and_explicit_dimensions_are_preserved(self):
        row = self.manifest['replacements'][0]
        row.update(set={'type': 'roundtable', 'name': '圆桌测试', 'w': 1000, 'd': 1000,
                        'height': 700, 'rot': 17}, dimensionsEvidence='自制测试工作尺度',
                   placementEvidence='测试显式旋转')
        result, report = self.apply()
        self.assertTrue(report['ok'], report)
        self.assertEqual(result['furniture'][0]['type'], 'roundtable')
        self.assertEqual(result['furniture'][0]['rot'], 17)
        self.assertEqual(report['items'][0]['aspect']['status'], 'nonuniform')
        self.assertEqual(result['furniture'][1:], self.original['furniture'][1:])

    def test_duplicates_unknown_fields_and_keep_asset_are_rejected(self):
        variants = []
        duplicate = copy.deepcopy(self.manifest)
        duplicate['replacements'].append(copy.deepcopy(duplicate['replacements'][0]))
        variants.append(duplicate)
        unknown = copy.deepcopy(self.manifest)
        unknown['replacements'][0]['set'] = {'id': 'NEW'}
        variants.append(unknown)
        keep_asset = copy.deepcopy(self.manifest)
        keep_asset['replacements'][1]['asset'] = 'asset.json'
        variants.append(keep_asset)
        for manifest in variants:
            result, report = replace_plan(self.plan, manifest, self.directory)
            self.assertIsNone(result)
            self.assertFalse(report['ok'])
        self.assertEqual(self.plan, self.original)

    def test_real_projection_collision_rejects_new_target_position(self):
        row = self.manifest['replacements'][0]
        row.update(set={'cx': 4500, 'cy': 1500}, placementEvidence='故意移到床上测试拒绝')
        result, report = self.apply()
        self.assertIsNone(result)
        self.assertFalse(report['placementScreen']['ok'])
        self.assertEqual(self.plan, self.original)

    def test_unknown_overlap_id_cannot_disable_screening(self):
        self.manifest['replacements'][0]['allowOverlap'] = [{'with': '*', 'reason': '不允许通配符'}]
        result, report = self.apply()
        self.assertIsNone(result)
        self.assertIn('叠放对象', str(report['errors']))

    def test_complete_export_envelope_and_atomic_bundle_match_html(self):
        source = self.directory / 'export.json'
        source.write_text(json.dumps({'savedAt': 'test', 'plan': self.plan}))
        self.assertEqual(read_plan(source), self.plan)
        result, report = self.apply()
        target = self.directory / 'bundle'
        saved = write_bundle(self.plan, result, self.manifest, report, target)
        self.assertEqual(saved['status'], 'written_pending_browser')
        self.assertEqual(json.loads((target / 'plan.json').read_text()), result)
        self.assertEqual(json.loads((target / 'before.plan.json').read_text()), self.original)
        payload = (target / 'index.html').read_text().split('/* PLAN_DATA_START */')[1].split('/* PLAN_DATA_END */')[0]
        self.assertEqual(json.loads(payload), result)
        with self.assertRaisesRegex(ValueError, '已存在'):
            write_bundle(self.plan, result, self.manifest, report, target)

    def test_build_failure_leaves_no_partial_bundle_or_changed_source(self):
        result, report = self.apply()
        output = self.directory / 'failed-bundle'
        with patch('replace_furniture.build', side_effect=ValueError('故意构建失败')):
            with self.assertRaises(ValueError):
                write_bundle(self.plan, result, self.manifest, report, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.directory.glob('.failed-bundle-*')))
        self.assertEqual(self.plan, self.original)

    def test_portable_bundle_replays_after_original_asset_is_removed(self):
        result, report = self.apply()
        delivery = self.directory / 'portable'
        write_bundle(self.plan, result, self.manifest, report, delivery)
        (self.directory / 'asset.json').unlink()
        snapshot = json.loads((delivery / 'manifest.json').read_text())
        self.assertEqual(snapshot['replacements'][0]['asset'], 'assets/A_CHAIR.json')
        replay, check = replace_plan(read_plan(delivery / 'before.plan.json'), snapshot, delivery)
        self.assertTrue(check['ok'], check)
        self.assertEqual(replay, result)

    def test_complete_state_with_demolition_or_measurements_is_not_silently_lost(self):
        source = self.directory / 'state.json'
        for state in ({'demolished': ['w2']}, {'measures': [{'a': [0, 0], 'b': [500, 0]}]}):
            source.write_text(json.dumps({'plan': self.plan, **state}))
            with self.assertRaisesRegex(ValueError, '不能丢弃'):
                read_plan(source)

    def test_dry_run_creates_no_files_then_cli_writes_same_decisions(self):
        source = self.directory / 'plan.json'
        manifest = self.directory / 'manifest.json'
        output = self.directory / 'new-version'
        source.write_text(json.dumps(self.plan))
        manifest.write_text(json.dumps(self.manifest))
        command = [sys.executable, str(SCRIPTS / 'replace_furniture.py'), '--plan', str(source),
                   '--manifest', str(manifest), '--output', str(output)]
        dry = subprocess.run(command + ['--dry-run'], capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
        self.assertEqual(json.loads(dry.stdout)['status'], 'dry_run_passed_pending_browser')
        self.assertFalse(output.exists())
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(json.loads(run.stdout)['browserVerification']['status'], 'not_run')
        self.assertTrue((output / 'index.html').exists())
        self.assertEqual(json.loads(source.read_text()), self.original)


if __name__ == '__main__':
    unittest.main()
