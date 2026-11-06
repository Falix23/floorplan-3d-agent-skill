import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plan_tools', ROOT/'skills/floorplan-image/scripts/plan_tools.py')
tools = importlib.util.module_from_spec(spec); spec.loader.exec_module(tools)
from building import assemble
from reconstruct import from_pixels
from replace_furniture import replace_plan, write_bundle
from test_furniture_assets import fixture_asset


class MultiFloorTests(unittest.TestCase):
    def setUp(self):
        self.b = json.loads((ROOT/'examples/multi-floor-duplex.json').read_text())

    def test_duplex_and_basement_building(self):
        self.assertTrue(any('估计' in w for w in tools.validate(self.b)))
        tools.validate(json.loads((ROOT/'examples/multi-floor-villa.json').read_text()))

    def test_floor_scoped_ids_can_repeat(self):
        self.assertEqual(self.b['floors'][0]['plan']['furniture'][0]['id'], self.b['floors'][1]['plan']['furniture'][0]['id'])
        tools.validate(self.b)

    def test_duplicate_floor_and_missing_alignment_are_rejected(self):
        for mutate in (lambda b: b['floors'][1].update(id='F1'), lambda b: b['floors'][0].pop('alignment')):
            b=copy.deepcopy(self.b);mutate(b)
            with self.assertRaises(ValueError): tools.validate(b)

    def test_floor_height_cannot_intersect_upper_slab(self):
        self.b['floors'][1]['elevation']=2900
        with self.assertRaisesRegex(ValueError,'侵入'): tools.validate(self.b)

    def test_stair_rise_and_floor_references(self):
        b=copy.deepcopy(self.b);b['stairs'][0]['flights'][0]['rise']=1000
        with self.assertRaisesRegex(ValueError,'总升高'): tools.validate(b)
        b=copy.deepcopy(self.b);b['stairs'][0]['toFloorId']='missing'
        with self.assertRaisesRegex(ValueError,'不存在'): tools.validate(b)

    def test_stair_cannot_pass_through_upper_slab(self):
        self.b['floors'][1]['plan']['rooms'][2].pop('holes')
        with self.assertRaisesRegex(ValueError,'楼板封住'): tools.validate(self.b)

    def test_both_edges_of_stair_require_opening(self):
        self.b['floors'][1]['plan']['rooms'][2]['holes'][0][1][0]=7300
        self.b['floors'][1]['plan']['rooms'][2]['holes'][0][2][0]=7300
        with self.assertRaisesRegex(ValueError,'楼板封住'): tools.validate(self.b)

    def test_u_stair_requires_intermediate_landing(self):
        self.b['stairs'][0]['landings']=[]
        with self.assertRaisesRegex(ValueError,'连接平台'): tools.validate(self.b)

    def test_furniture_and_label_cannot_sit_in_void(self):
        upper=self.b['floors'][1]['plan'];upper['furniture'][0].update(cx=6500,cy=5000)
        with self.assertRaisesRegex(ValueError,'洞口'): tools.validate(self.b)
        upper['furniture']=[];upper['rooms'][2]['at']=[6500,5000]
        with self.assertRaisesRegex(ValueError,'标签点'): tools.validate(self.b)

    def test_self_intersecting_touching_and_nested_voids_rejected(self):
        p=self.b['floors'][1]['plan'];r=p['rooms'][2]
        for holes in ([[[5300,2700],[7700,7900],[5300,7900],[7700,2700]]],
                      [[[5200,2700],[7700,2700],[7700,7900],[5200,7900]]],
                      [r['holes'][0],[[5500,3000],[6000,3000],[6000,3500],[5500,3500]]]):
            q=copy.deepcopy(p);q['rooms'][2]['holes']=holes
            with self.assertRaises(ValueError): tools.validate(q)

    def test_pixels_convert_voids_with_same_origin_and_scale(self):
        d={'id':'p','title':'p','image':{'width':400,'height':400},'originPx':[10,20],
           'calibration':{'mmPerPixel':10,'status':'estimated'},
           'rooms':[{'id':'R1','name':'room','mat':'wood','polyPx':[[10,20],[310,20],[310,320],[10,320]],
                     'holesPx':[[[110,120],[210,120],[210,220],[110,220]]]}],'walls':[],'furniture':[]}
        p=from_pixels(d);self.assertEqual(p['rooms'][0]['holes'][0][0],[1000,1000]);tools.validate(p)
        del d['calibration'];d['rooms'][0]['calibrationAreaM2']=8
        inferred=from_pixels(d)
        self.assertEqual(inferred['reference']['mmPerPixel'],10)

    def test_assemble_uses_per_floor_input_and_does_not_mutate_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);manifest=copy.deepcopy(self.b)
            for f in manifest['floors']:
                (base/(f['id']+'.json')).write_text(json.dumps(f.pop('plan')));f['planFile']=f['id']+'.json'
            original=copy.deepcopy(manifest);result,report=assemble(manifest,base)
            self.assertEqual(result,self.b);self.assertEqual(original,manifest);self.assertEqual(len(report['floors']),2)

    def test_build_embeds_whole_building_and_preserves_input(self):
        original=copy.deepcopy(self.b)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'building.html';tools.build(self.b,out)
            payload=out.read_text().split('/* PLAN_DATA_START */')[1].split('/* PLAN_DATA_END */')[0]
            self.assertEqual(json.loads(payload),self.b)
        self.assertEqual(original,self.b)

    def tread_stair(self):
        # 非图片数据：仅验证显式转角踏步的拓扑与标高。
        # 六级工程夹具仅检查连接关系；500mm升高不代表实际楼梯设计。
        self.b['stairs']=[{'id':'S1','name':'多边形踏步测试','fromFloorId':'F1','toFloorId':'F2','treads':[
            {'poly':[[6750,4500+i*500],[7650,4500+i*500],[7650,5000+i*500],[6750,5000+i*500]],'rise':500} for i in range(6)]}]
        return self.b['stairs'][0]

    def test_explicit_treads_validate_height_shared_edges_and_upper_opening(self):
        s=self.tread_stair();tools.validate(self.b)
        s['treads'][2]['poly']=[[x+2000,y] for x,y in s['treads'][2]['poly']]
        with self.assertRaisesRegex(ValueError,'共享边'):tools.validate(self.b)
        s=self.tread_stair();s['treads'][0]['rise']=400
        with self.assertRaisesRegex(ValueError,'总升高'):tools.validate(self.b)
        s=self.tread_stair();s['flights']=[]
        with self.assertRaisesRegex(ValueError,'不能混合'):tools.validate(self.b)

    def test_explicit_tread_exit_cannot_be_hidden_by_upper_floor(self):
        self.tread_stair();self.b['floors'][1]['plan']['rooms'][2].pop('holes')
        with self.assertRaisesRegex(ValueError,'上层楼板'):tools.validate(self.b)

    def test_multi_floor_replacement_is_scoped_and_portable(self):
        original=copy.deepcopy(self.b)
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);asset=fixture_asset();(base/'asset.json').write_text(json.dumps(asset))
            manifest={'schemaVersion':1,'replacements':[{'floorId':'F2','itemId':'I1','decision':'replace','asset':'asset.json','reason':'自制网格隔离测试'}]}
            result,report=replace_plan(self.b,manifest,base)
            self.assertTrue(report['ok'],report)
            self.assertNotIn('assetId',result['floors'][0]['plan']['furniture'][0])
            self.assertIn('assetId',result['floors'][1]['plan']['furniture'][0])
            write_bundle(self.b,result,manifest,report,base/'bundle')
            portable=json.loads((base/'bundle/manifest.json').read_text())
            self.assertTrue((base/'bundle'/portable['replacements'][0]['asset']).is_file())
        self.assertEqual(self.b,original)

    def test_multi_floor_batch_rolls_back_if_another_floor_fails(self):
        original=copy.deepcopy(self.b)
        manifest={'schemaVersion':1,'replacements':[{'floorId':'F1','itemId':'I1','decision':'keep','reason':'保留'}, {'floorId':'F2','itemId':'absent','decision':'keep','reason':'无效目标'}]}
        result,report=replace_plan(self.b,manifest,Path('.'))
        self.assertFalse(report['ok']);self.assertIsNone(result);self.assertEqual(self.b,original)
        del manifest['replacements'][0]['floorId']
        self.assertFalse(replace_plan(self.b,manifest,Path('.'))[1]['ok'])


if __name__ == '__main__': unittest.main()
