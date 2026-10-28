from __future__ import annotations
import unittest
from unittest.mock import patch
from mixcut.auto_packaging import GENTLE_PROFILE,effect_params,validate_auto,_parameter_metadata
from mixcut.common import PipelineError
from mixcut.face_cover import append_face_cover


class GentleEffectTests(unittest.TestCase):
    def test_english_parameters_receive_small_values(self):
        values=effect_params('光斑虚化',25,GENTLE_PROFILE)
        params=dict(zip([p[0] for p in _parameter_metadata()['光斑虚化']],values))
        self.assertEqual(params['effects_adjust_luminance'],12)
        self.assertEqual(params['effects_adjust_intensity'],18)
        self.assertEqual(effect_params('轻微抖动',25,GENTLE_PROFILE),[2,20])
        # The final upstream parameter is sharpening, not distortion. Retain
        # its native value so controlling movement does not blur the product.
        self.assertEqual(effect_params('左右摇晃',25,GENTLE_PROFILE),[2,0,20,None])
        self.assertEqual(effect_params('光斑虚化',1,GENTLE_PROFILE)[1:3],[1,1])

    def test_legacy_english_defaults_remain_readable(self):
        self.assertEqual(effect_params('光斑虚化',25,'legacy'),[None]*4)

    def test_profile_requires_both_groups_and_rejects_unlisted_effects(self):
        with self.assertRaises(PipelineError):effect_params('霓虹摇摆',8,GENTLE_PROFILE)
        with self.assertRaises(PipelineError):validate_auto({'music_source':'cloud','effect_profile':GENTLE_PROFILE,'effect_categories':['氛围']})

    def test_face_switch_is_off_by_default_and_skips_only_opening(self):
        template={'name':'拽酷墨镜','material':{'id':'old','name':'拽酷墨镜','resource_id':'123456789','effect_id':'456','type':'face_effect','path':'C:/cache/face'},
            'segment':{'id':'old','material_id':'old','target_timerange':{'start':0,'duration':3},'extra_material_refs':['unresolved']}}
        base={'materials':{},'tracks':[]}
        self.assertEqual(append_face_cover(base,{},2,10),{'enabled':False})
        self.assertEqual(base,{'materials':{},'tracks':[]})
        with patch('mixcut.face_cover.face_cover_template',return_value=template):
            result=append_face_cover(base,{'face_cover_enabled':True},2_000_000,10_000_000)
        self.assertEqual(result['start_seconds'],2)
        effect=base['tracks'][0]['segments'][0]
        self.assertEqual(effect['target_timerange'],{'start':2_000_000,'duration':8_000_000})
        material=base['materials']['video_effects'][0]
        self.assertEqual(effect['material_id'],material['id'])
        self.assertEqual(effect['extra_material_refs'],[])
        self.assertNotIn('path',material)
        self.assertEqual(material['apply_target_type'],2)

    def test_candidate_pool_has_forty_atmosphere_and_eighteen_motion(self):
        from mixcut.auto_packaging import effect_pool
        pool=effect_pool({'effect_profile':GENTLE_PROFILE,'effect_categories':['氛围','动感'],'include_vip':False})
        self.assertEqual(sum(e['category']=='氛围' for e in pool),40)
        self.assertEqual(sum(e['category']=='动感' for e in pool),18)
        self.assertEqual(len({e['resource_id'] for e in pool}),58)
