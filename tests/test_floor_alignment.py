import copy
import base64
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('floor_alignment', ROOT/'skills/floorplan-image/scripts/floor_alignment.py')
alignment = importlib.util.module_from_spec(spec); spec.loader.exec_module(alignment)


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ('ref.png', 'moving.png'):
            Image.new('RGB', (250, 250), 'white').save(self.base/name)
        scale, angle = 1.2, .1
        a, b = scale*math.cos(angle), scale*math.sin(angle)
        self.matrix = [[a, -b, 20], [b, a, 5], [0, 0, 1]]
        def row(i, p):
            x, y = p
            return {'id':str(i), 'movingPx':[x,y], 'referencePx':[a*x-b*y+20,b*x+a*y+5]}
        self.pair = {'id':'F1-F2',
                     'reference':{'floorId':'F1','imageFile':'ref.png','mmPerPixel':10,'scaleEvidence':'自制测试尺度'},
                     'moving':{'floorId':'F2','imageFile':'moving.png'},
                     'fitPoints':[row(1,[10,10]), row(2,[90,10])],
                     'checkPoints':[row(3,[10,90]), row(4,[90,90])],
                     'plannedTransform':self.matrix,'limits':{'rmsCheckPx':1,'maxCheckPx':2}}

    def run_pair(self, pair=None):
        return alignment.analyze({'schemaVersion':1,'pairs':[pair or self.pair]}, self.base)

    def test_known_rotation_scale_translation_and_separate_holdouts(self):
        report = self.run_pair(); p = report['pairs'][0]
        self.assertEqual(report['status'], 'within_working_limits')
        self.assertAlmostEqual(p['proposal']['scale'], 1.2)
        self.assertAlmostEqual(p['proposal']['rotationDegrees'], math.degrees(.1))
        self.assertLess(p['checks']['maxPx'], 1e-10)
        self.assertEqual(p['fit']['count'], 2); self.assertEqual(p['checks']['count'], 2)
        self.assertEqual(p['reference']['sha256'], p['moving']['sha256'])

    def test_bad_holdout_is_not_hidden_by_zero_fitting_error(self):
        self.pair['checkPoints'][0]['referencePx'][0] += 12
        p = self.run_pair()['pairs'][0]
        self.assertLess(p['fit']['maxPx'], 1e-10)
        self.assertGreater(p['checks']['maxPx'], 11)
        self.assertEqual(p['status'], 'review_required')

    def test_good_proposal_does_not_pass_bad_current_alignment_or_mutate_input(self):
        self.pair['plannedTransform'] = [[1,0,0],[0,1,0],[0,0,1]]
        before = copy.deepcopy(self.pair); p = self.run_pair()['pairs'][0]
        self.assertEqual(self.pair, before)
        self.assertTrue(p['proposal']['withinWorkingLimits'])
        self.assertFalse(p['currentAlignment']['withinWorkingLimits'])
        self.assertEqual(p['status'], 'review_required')
        self.assertFalse(p['proposal']['appliedToBuilding'])

    def test_proposal_without_current_transform_is_not_acceptance(self):
        self.pair.pop('plannedTransform')
        self.assertEqual(self.run_pair()['pairs'][0]['status'], 'proposal_only')
        self.assertEqual(self.run_pair()['status'], 'review_required')

    def test_duplicate_id_or_renamed_fit_point_cannot_be_independent_check(self):
        for mutation in ('id', 'coordinate'):
            p = copy.deepcopy(self.pair)
            if mutation == 'id': p['checkPoints'][0]['id'] = p['fitPoints'][0]['id']
            else: p['checkPoints'][0]['movingPx'] = p['fitPoints'][0]['movingPx'][:]
            with self.assertRaisesRegex(ValueError, '重复'): self.run_pair(p)

    def test_collinear_landmarks_require_second_direction_evidence(self):
        for i, row in enumerate(self.pair['fitPoints']+self.pair['checkPoints']):
            row['referencePx'] = [10+20*i,20]; row['movingPx'] = [20+20*i,20]
        with self.assertRaisesRegex(ValueError, '共线'): self.run_pair()

    def test_mirror_is_not_silently_applied(self):
        for row in self.pair['fitPoints']+self.pair['checkPoints']:
            row['referencePx'] = [200-row['movingPx'][0],row['movingPx'][1]]
        self.pair.pop('plannedTransform')
        self.assertFalse(self.run_pair()['pairs'][0]['proposal']['withinWorkingLimits'])

    def test_shear_or_mirror_current_transform_is_rejected(self):
        for matrix in ([[1,.2,0],[0,1,0],[0,0,1]],[[-1,0,0],[0,1,0],[0,0,1]]):
            self.pair['plannedTransform'] = matrix
            with self.assertRaisesRegex(ValueError, '镜像/剪切'): self.run_pair()

    def test_missing_scale_evidence_invalid_point_or_missing_limits_is_rejected(self):
        for change in (lambda p:p['reference'].pop('scaleEvidence'),
                       lambda p:p['fitPoints'][0].update(movingPx=[float('nan'),5]),
                       lambda p:p['limits'].update(maxCheckPx=0)):
            p=copy.deepcopy(self.pair);change(p)
            with self.assertRaises(ValueError): self.run_pair(p)

    def test_pixel_only_report_and_embedded_source_overlays(self):
        self.pair['reference'].pop('mmPerPixel')
        report = self.run_pair(); target = self.base/'review.html'
        alignment.write_review(report,target)
        self.assertNotIn('rmsDrawingEquivalentMm',report['pairs'][0]['checks'])
        self.assertIn('未参与拟合',target.read_text())
        self.assertIn('data:image/png;base64,',(self.base/'F1-F2-current.svg').read_text())
        json.dumps(report,allow_nan=False)

    def building_pair(self):
        data='data:image/png;base64,'+base64.b64encode((self.base/'ref.png').read_bytes()).decode()
        floors=[]
        for fid,mm,offset in [('F1',10,[0,0]),('F2',12,[200,50])]:
            floors.append({'id':fid,'offset':offset,'plan':{'reference':{'mmPerPixel':mm,'originPx':[0,0],
                'width':250,'height':250,'dataUrl':data}}})
        (self.base/'building.json').write_text(json.dumps({'schemaVersion':2,'floors':floors}))
        self.pair['buildingFile']='building.json';self.pair.pop('plannedTransform')
        for row in self.pair['fitPoints']+self.pair['checkPoints']:
            x,y=row['movingPx'];row['referencePx']=[x*1.2+20,y*1.2+5]

    def test_current_transform_comes_from_building_and_source_hashes(self):
        self.building_pair();p=self.run_pair()['pairs'][0]
        self.assertEqual(p['status'],'within_working_limits')
        self.assertTrue(p['currentAlignment']['provenance']['embeddedSourcesMatch'])
        self.assertEqual(p['currentAlignment']['provenance']['kind'],'building_derived')
        data=json.loads((self.base/'building.json').read_text());data['floors'][1]['offset'][0]+=100
        (self.base/'building.json').write_text(json.dumps(data))
        p=self.run_pair()['pairs'][0]
        self.assertEqual(p['status'],'review_required')
        self.assertAlmostEqual(p['currentAlignment']['checks']['maxPx'],10)
        self.assertTrue(p['proposal']['withinWorkingLimits'])

    def test_wrong_embedded_source_cannot_validate_current_building(self):
        self.building_pair();Image.new('RGB',(250,250),'red').save(self.base/'moving.png')
        with self.assertRaisesRegex(ValueError,'原图与配准输入不一致'):self.run_pair()

    def test_declared_fit_cannot_override_different_current_building(self):
        self.building_pair();self.pair['plannedTransform']=[[1,0,0],[0,1,0],[0,0,1]]
        with self.assertRaisesRegex(ValueError,'实际标定/offset不一致'):self.run_pair()

    def test_cli_keeps_review_report_but_returns_nonzero_for_bad_current_alignment(self):
        self.pair['plannedTransform']=[[1,0,0],[0,1,0],[0,0,1]]
        manifest=self.base/'input.json';manifest.write_text(json.dumps({'schemaVersion':1,'pairs':[self.pair]}))
        result=subprocess.run([sys.executable,str(ROOT/'skills/floorplan-image/scripts/floor_alignment.py'),
            str(manifest),'--report',str(self.base/'report.json')],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads((self.base/'report.json').read_text())['status'],'review_required')


if __name__ == '__main__':
    unittest.main()
