import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/floorplan-image/scripts'))
from plan_tools import validate
from reconstruct import refine_plan,from_pixels,audit_plan
from furniture_constraints import validate_semantics


class FurnitureConstraintTests(unittest.TestCase):
    def setUp(self):
        self.p={'schemaVersion':1,'id':'semantics','title':'自制约束夹具','units':'mm','height':2800,
            'scale':{'status':'relative'},'furnitureReview':{'required':True},
            'rooms':[{'id':'R1','name':'房间','mat':'wood','poly':[[0,0],[4000,0],[4000,4000],[0,4000]]}],
            'walls':[[0,-200,4000,0,'u'],[-200,0,0,4000,'u'],[4000,0,4200,4000,'u'],[0,4000,4000,4200,'u']],
            'windows':[],'doors':[],'slides':[],
            'furniture':[{'id':'I1','type':'bed','name':'床','cx':2000,'cy':1100,'w':1600,'d':2000,'rot':0,
                'orientationConstraint':{'status':'observed','front':[0,1],'feature':'headboard','maxAngleDegrees':5,'evidence':'自制源图床头在北'},
                'placement':{'mode':'wall','roomId':'R1','edgeIndex':0,'side':'back','gap':0,'maxAdjust':120,'evidence':'自制北墙接触'}}]}

    def test_half_turn_with_same_bbox_is_rejected(self):
        self.p['furniture'][0]['rot']=180
        with self.assertRaisesRegex(ValueError,'朝向'):validate(self.p)

    def test_missing_orientation_or_placement_cannot_pass_strict_photo_review(self):
        for key in ['orientationConstraint','placement']:
            p=copy.deepcopy(self.p);p['furniture'][0].pop(key)
            with self.assertRaisesRegex(ValueError,'缺少'):validate(p)

    def test_unknown_direction_is_not_reported_as_identified(self):
        self.p['furniture'][0]['orientationConstraint']={'status':'unknown','evidence':'无法辨认床头'}
        with self.assertRaisesRegex(ValueError,'待确认'):validate(self.p)

    def test_inferred_direction_is_exposed_and_still_cannot_be_reversed(self):
        self.p['furniture'][0]['orientationConstraint']['status']='inferred'
        self.assertEqual(validate_semantics(self.p)['inferredDirections'],['I1'])
        self.p['furniture'][0]['rot']=180
        with self.assertRaisesRegex(ValueError,'朝向'):validate(self.p)

    def test_anchor_refines_gap_and_exposes_reported_error(self):
        p,rows=refine_plan(self.p);self.assertEqual(rows[0]['distanceMm'],100)
        report=audit_plan(p);self.assertTrue(report['semanticReview']['ok'])
        self.assertEqual(report['furniture'][0]['anchorErrorMm'],0)
        self.assertAlmostEqual(report['semanticReview']['furniture'][0]['sourceOrientationErrorDegrees'],0)

    def test_no_explicit_maximum_correction_is_rejected(self):
        self.p['furniture'][0]['placement'].pop('maxAdjust')
        with self.assertRaisesRegex(ValueError,'显式'):validate(self.p)

    def test_validator_rejects_large_contact_gap_before_building(self):
        self.p['furniture'][0]['cy']=2000
        with self.assertRaisesRegex(ValueError,'间隙'):validate(self.p)

    def test_default_anchor_cannot_treat_window_as_solid_wall(self):
        self.p['walls']=self.p['walls'][1:];self.p['windows']=[[0,-200,4000,0]]
        with self.assertRaisesRegex(ValueError,'缺少实体墙'):refine_plan(self.p)

    def test_explicit_window_projection_requires_disclosed_unverified_height(self):
        self.p['walls']=self.p['walls'][1:];self.p['windows']=[[0,-200,4000,0]]
        a=self.p['furniture'][0]['placement'];a['support']='window-plane'
        with self.assertRaisesRegex(ValueError,'竖向'):validate(self.p)
        a['verticalVerified']=False
        p,_=refine_plan(self.p);self.assertEqual(audit_plan(p)['furniture'][0]['anchorErrorMm'],0)
        self.assertEqual(audit_plan(p)['semanticReview']['furniture'][0]['support'],'window-plane')

    def test_free_standing_divider_is_not_forced_to_wall(self):
        f=self.p['furniture'][0];f['type']='wardrobe';f['placement']={'mode':'free','evidence':'原图独立分隔柜，后面没有墙'}
        p,rows=refine_plan(self.p);self.assertEqual(p['furniture'][0]['cy'],1100);self.assertEqual(rows,[])

    def test_face_away_from_target_is_rejected_with_identical_footprint(self):
        f=self.p['furniture'][0];f['type']='sofa';f['placement']={'mode':'free','evidence':'居中沙发'}
        f['facingTarget']={'targetId':'I2','maxAngleDegrees':60,'evidence':'茶几在座面前'}
        self.p['furniture'].append({'id':'I2','type':'coffeetable','name':'茶几','cx':2000,'cy':500,'w':500,'d':400,'rot':0})
        with self.assertRaisesRegex(ValueError,'背对'):validate(self.p)

    def test_missing_target_and_invalid_vectors_are_rejected(self):
        f=self.p['furniture'][0];f['facingTarget']={'targetId':'missing','maxAngleDegrees':60,'evidence':'未知目标'}
        with self.assertRaisesRegex(ValueError,'目标'):validate(self.p)
        f.pop('facingTarget');f['orientationConstraint']['front']=[0,0]
        with self.assertRaisesRegex(ValueError,'front向量'):validate(self.p)

    def test_authored_edits_preserve_history_without_reapplying_source_heading(self):
        f=self.p['furniture'][0];f['rot']=90;f['orientationConstraint']={'status':'released','previousSource':f['orientationConstraint'],'evidence':'用户主动旋转'}
        f['placement']={'mode':'free','evidence':'用户主动摆放'}
        p,rows=refine_plan(self.p);self.assertEqual(p['furniture'][0]['rot'],90);self.assertEqual(rows,[])

    def test_pixel_roundtrip_keeps_policy_and_semantic_evidence(self):
        o={'id':'pixels','title':'观测','image':{'width':400,'height':400},'calibration':{'mmPerPixel':10},
            'furnitureReview':{'required':True},'rooms':[{'id':'R1','name':'房间','mat':'wood','polyPx':[[0,0],[400,0],[400,400],[0,400]]}],
            'walls':[],'windows':[],'furniture':[{'id':'I1','type':'bed','name':'床','bboxPx':[100,100,250,300],'rot':0,
                'orientationConstraint':self.p['furniture'][0]['orientationConstraint'],
                'placement':{'mode':'free','evidence':'原图保留明显间隙'}}]}
        p=from_pixels(o);validate(p);self.assertTrue(p['furnitureReview']['required'])
        self.assertEqual(p['furniture'][0]['orientationConstraint']['front'],[0,1])

    def test_pixel_recognition_defaults_to_required_semantic_evidence(self):
        o={'id':'missing-evidence','title':'未记录朝向','image':{'width':400,'height':400},
           'calibration':{'mmPerPixel':10},'rooms':[{'id':'R1','name':'房间','mat':'wood','polyPx':[[0,0],[400,0],[400,400],[0,400]]}],
           'walls':[],'furniture':[{'id':'I1','type':'bed','name':'床','bboxPx':[100,100,250,300],'rot':0}]}
        p=from_pixels(o);self.assertTrue(p['furnitureReview']['required'])
        with self.assertRaisesRegex(ValueError,'缺少'):validate(p)


if __name__=='__main__':unittest.main()
