from pathlib import Path
import copy
import shutil
import tempfile
import unittest
from mixcut.common import ROOT,Project,PipelineError,read_json,write_json,run_command,runtime_tool,file_hash,object_hash
from mixcut.harvest import harvest_music
from mixcut.local_music import local_music_pool,verify_song,replace_batch_music
from mixcut.auto_packaging import attach_auto_packaging,frozen_packaging
from mixcut.plan_batches import record_batch

class LocalMusicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=ROOT/".runtime/local-music-tests";base.mkdir(exist_ok=True)
        cls.fixtures=tempfile.TemporaryDirectory(dir=base);cls.source=Path(cls.fixtures.name)
        for i,duration in enumerate([2,3,4]):
            run_command([runtime_tool("ffmpeg"),"-v","error","-y","-f","lavfi","-i",f"sine=frequency={500+i*100}:duration={duration}","-c:a","aac","-f","adts",str(cls.source/f"{i}.mp3")])
        cls.video=cls.source/"body.mp4"
        run_command([runtime_tool("ffmpeg"),"-v","error","-y","-f","lavfi","-i","color=c=red:s=160x284:r=12:d=1","-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=1","-c:v","libx264","-preset","ultrafast","-c:a","aac","-shortest",str(cls.video)])

    @classmethod
    def tearDownClass(cls):cls.fixtures.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/".runtime/local-music-tests");self.root=Path(self.tmp.name)
        self.source=self.root/"source";self.source.mkdir()
        for path in type(self).source.glob("*.mp3"):shutil.copy2(path,self.source/path.name)
        self.library=self.root/"library";harvest_music(source=self.source,target=self.library,min_seconds=1)
        cfg=copy.deepcopy(read_json(ROOT/"config.example.json"));cfg.update(workflow="folder_mix",workspace="work")
        cfg["render"].update(width=160,height=284,fps=12,music="",output_mode="jianying",corner_overlay={"enabled":False},
            jianying={"selection_mode":"auto","music_source":"local_index","local_music_index":str(self.library/"index.json"),
                "effect_profile":"gentle_3_2","effect_categories":["氛围","动感"],"include_vip":False,"effect_strength":18,"effect_count":5})
        self.config=self.root/"config.json";write_json(self.config,cfg);self.p=Project(self.config)

    def tearDown(self):self.tmp.cleanup()

    def plan(self,identity="1"*16,cloud=False):
        from PIL import Image
        cfg=copy.deepcopy(self.p.config["render"]);images=[]
        if cloud:cfg["jianying"]["music_source"]="cloud"
        for i in range(4):
            path=self.root/f"photo{i}.png"
            if not path.exists():Image.new("RGB",(160,284),(i*60,40,20)).save(path)
            images.append({"path":str(path),"sha256":file_hash(path),"seconds":2})
        cfg.update(product_images=images,product_image=images[0]["path"],tail_seconds=8)
        plan={"mode":"folder_mix","id":identity,"signature":object_hash(identity),"product":"测试",
            "body_seconds":1,"clips":[{"path":str(type(self).video),"file_sha256":file_hash(type(self).video),"duration":1}],"render":cfg}
        attach_auto_packaging(self.p,plan);write_json(self.p.plans/(identity+".json"),plan)
        return plan

    def test_cache_aac_extension_is_corrected_and_duplicates_are_not_reimported(self):
        shutil.copy2(self.source/"0.mp3",self.source/"duplicate.mp3")
        (self.source/"broken.mp3").write_bytes(b"invalid audio")
        report=harvest_music(source=self.source,target=self.library,min_seconds=1)
        data=read_json(self.library/"index.json")
        self.assertEqual(len(data),3);self.assertEqual(report["counts"]["broken"],1)
        self.assertTrue(all(Path(i["path"]).suffix==".m4a" and i["fully_decoded"] for i in data))
        self.assertEqual([i["number"] for i in data],[1,2,3])
        for song in local_music_pool(self.p,{"local_music_index":str(self.library/"index.json")}):verify_song(song)

    def test_copied_library_is_portable_and_independent_of_source_cache(self):
        moved=self.root/"portable";shutil.copytree(self.library,moved)
        for path in self.source.glob("*.mp3"):path.unlink()
        songs=local_music_pool(self.p,{"local_music_index":str(moved/"index.json")})
        self.assertEqual(len(songs),3)
        self.assertTrue(all(Path(s["path"]).is_relative_to(moved) for s in songs))
        before=(moved/"index.json").read_bytes()
        with self.assertRaises(PipelineError):harvest_music(source=self.source,target=moved,min_seconds=1)
        self.assertEqual(before,(moved/"index.json").read_bytes())

    def test_frozen_local_playlist_covers_tail_once_and_only_final_song_is_cut(self):
        plan=self.plan();value=frozen_packaging(plan,plan["render"]);cursor=1_000_000
        self.assertGreaterEqual(len(value["music"]),2)
        self.assertEqual(len({s["id"] for s in value["music"]}),len(value["music"]))
        for song in value["music"]:
            self.assertEqual(song["start_us"],cursor);cursor+=song["duration_us"]
        self.assertEqual(cursor,9_000_000)
        self.assertTrue(all(s["duration_us"]==round(s["seconds"]*1e6) for s in value["music"][:-1]))

    def test_real_sdk_uses_local_audio_files_and_rejects_tampering_before_reuse(self):
        from mixcut.draft import export_draft
        plan=self.plan();result=export_draft(self.p,plan["id"])
        content=read_json(Path(result["draft_path"])/"draft_content.json")
        self.assertTrue(all(a["type"]=="extract_music" and Path(a["path"]).is_file() for a in content["materials"]["audios"]))
        bgm=next(t for t in content["tracks"] if t["type"]=="audio")
        self.assertEqual(bgm["segments"][0]["target_timerange"]["start"],1_000_000)
        self.assertEqual(sum(s["target_timerange"]["duration"] for s in bgm["segments"]),8_000_000)
        song=plan["render"]["packaging_plan"]["music"][0];Path(song["path"]).write_bytes(b"modified")
        with self.assertRaisesRegex(PipelineError,"本地配乐被修改"):export_draft(self.p,plan["id"])

    def test_replace_only_current_batch_music_preserves_effects_and_history(self):
        original=self.plan(cloud=True);history=self.plan("2"*16,cloud=True)
        record_batch(self.p,[original["id"]]);before=(self.p.plans/(history["id"]+".json")).read_bytes()
        report=replace_batch_music(self.p,index=str(self.library/"index.json"))
        updated=read_json(self.p.plans/(original["id"]+".json"))
        self.assertEqual(report["count"],1)
        for key in ["clips","body_seconds"]:self.assertEqual(updated[key],original[key])
        for key in ["product_images","corner_overlay","music_volume"]:self.assertEqual(updated["render"][key],original["render"][key])
        self.assertEqual(updated["render"]["packaging_plan"]["effects"],original["render"]["packaging_plan"]["effects"])
        self.assertEqual(before,(self.p.plans/(history["id"]+".json")).read_bytes())
        self.assertEqual(read_json(Path(report["backup"])/(original["id"]+".json")),original)

    def test_insufficient_library_does_not_modify_batch(self):
        plan=self.plan(cloud=True);record_batch(self.p,[plan["id"]])
        data=read_json(self.library/"index.json");write_json(self.library/"index.json",data[:1])
        snapshot=(self.p.plans/(plan["id"]+".json")).read_bytes()
        with self.assertRaisesRegex(PipelineError,"总时长不足"):replace_batch_music(self.p,index=str(self.library/"index.json"))
        self.assertEqual(snapshot,(self.p.plans/(plan["id"]+".json")).read_bytes())
