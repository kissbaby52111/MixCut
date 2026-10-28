from __future__ import annotations
import copy
from pathlib import Path
import shutil
import tempfile
import unittest
import math
import os
import struct
import subprocess
import wave
from unittest.mock import patch
from PIL import Image

from mixcut.common import ROOT, Project, PipelineError, read_json, write_json, run_command, runtime_tool
from mixcut.folder_mix import settings, create_default_folders
from mixcut.folder_service import FolderService
from mixcut.planning import make_plans
from mixcut.render import render_pending, render_plan


class FolderMixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=ROOT/".runtime/folder-tests";base.mkdir(exist_ok=True)
        cls.fixtures=tempfile.TemporaryDirectory(dir=base)
        cls.sources=[]
        for index,color in enumerate(["red","green","blue","yellow","purple"]):
            path=Path(cls.fixtures.name)/f"{index}.mp4"
            cmd=[runtime_tool("ffmpeg"),"-v","error","-nostdin","-y","-f","lavfi","-i",f"color=c={color}:s=160x284:r=12:d=1"]
            if index==0:cmd += ["-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=1"]
            cmd += ["-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p"]
            if index==0:cmd += ["-c:a","aac","-shortest"]
            run_command(cmd+[str(path)]);cls.sources.append(path)

    @classmethod
    def tearDownClass(cls):cls.fixtures.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/".runtime/folder-tests")
        self.root=Path(self.tmp.name)
        cfg=copy.deepcopy(read_json(ROOT/"config.example.json"))
        cfg.update(workflow="folder_mix",workspace="work")
        cfg.pop("folder_mix",None)
        cfg["render"].update(width=160,height=284,fps=12,preset="ultrafast",music="",effect_video="",meteor={"enabled":False})
        cfg["render"].update(output_mode="ffmpeg",jianying={"music_source":"none","effect_enabled":False,"effect_key":"流星雨"})
        self.config=self.root/"config.json";write_json(self.config,cfg)
        self.project=Project(self.config);create_default_folders(self.project)
        folders=settings(self.project);folders["image_seconds"]=1
        for slot,source in zip(folders["slots"],self.sources):
            shutil.copy2(source,Path(slot["folder"])/source.name)
        for index,color in enumerate([(230,50,60),(40,210,80),(50,80,230),(220,180,50)]):
            Image.new("RGB",(160,284),color).save(Path(folders["image_folder"])/f"{index}.png")
        cfg["folder_mix"]=folders;write_json(self.config,cfg)
        self.project=Project(self.config)

    def tearDown(self):self.tmp.cleanup()

    def values(self):
        cfg=self.project.config;render=cfg["render"]
        return {**copy.deepcopy(cfg["folder_mix"]),"count":2,"width":160,"height":284,"fps":12,
            "music":"","effect_video":"","music_volume":.2,"original_audio_volume":1,
            "meteor_enabled":False,"scope":"all","intensity":.5}

    def music(self):
        path=self.root/"tone.wav"
        with wave.open(str(path),"wb") as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000)
            out.writeframes(b"".join(struct.pack("<h",int(9000*math.sin(2*math.pi*880*index/48000))) for index in range(72000)))
        return path

    def native_plan(self,source="none",cloud_seconds=1.5):
        from mixcut.jianying_assets import catalog
        music=dict(catalog("music")[0]);music["seconds"]=cloud_seconds
        self.project.config["render"].update(output_mode="jianying",music=str(self.music()) if source=="local" else "",
            original_audio_volume=.2,meteor={"enabled":True,"scope":"all"},
            jianying={"music_source":source,"cloud_music":music,"effect_enabled":True,"effect_key":"流星雨"})
        self.project.save_config()
        return make_plans(self.project,1)[0]

    def assert_tail_boundary(self,plan,result):
        content=read_json(Path(result["draft_path"])/"draft_content.json")
        main=next(track for track in content["tracks"] if track["type"]=="video")
        effect_ids={item["id"] for item in content["materials"]["video_effects"]}
        for segment in main["segments"][:5]:
            self.assertEqual(segment["volume"],1)
            self.assertFalse(effect_ids.intersection(segment.get("extra_material_refs",[])))
        for segment in main["segments"][5:]:
            self.assertTrue(effect_ids.intersection(segment["extra_material_refs"]))
        audio=next(track for track in content["tracks"] if track["type"]=="audio")
        self.assertEqual(audio["segments"][0]["target_timerange"]["start"],5_000_000)
        self.assertEqual(sum(segment["target_timerange"]["duration"] for segment in audio["segments"]),4_000_000)
        for segment in audio["segments"]:
            interval=segment["target_timerange"]
            self.assertGreaterEqual(interval["start"],5_000_000)
            self.assertLessEqual(interval["start"]+interval["duration"],9_000_000)
        self.assertEqual(result["music_start_seconds"],5)
        return content

    def auto_plan(self,seconds=255):
        self.project.config["folder_mix"]["image_seconds"]=seconds
        self.project.config["render"].update(output_mode="jianying",music="",
            jianying={"selection_mode":"auto","music_source":"cloud","cloud_music":{},
                "effect_categories":["氛围","动感"],"effect_count":5,"effect_strength":25,"include_vip":False})
        self.project.save_config()
        return make_plans(self.project,1)[0]

    def test_auto_plan_has_five_distinct_style_effects_and_multiple_songs(self):
        from mixcut.auto_packaging import effect_pool,frozen_packaging
        plan=self.auto_plan()
        packaging=frozen_packaging(plan,plan["render"])
        self.assertEqual(packaging["duration_us"],1_020_000_000)
        self.assertEqual(len({item["resource_id"] for item in packaging["effects"]}),5)
        self.assertEqual(sum(item["category"]=="氛围" for item in packaging["effects"]),3)
        self.assertEqual(sum(item["category"]=="动感" for item in packaging["effects"]),2)
        eligible={item["key"] for item in effect_pool(packaging["policy"])}
        for item in packaging["effects"]:
            self.assertIn(item["key"],eligible)
            self.assertFalse(item["vip"])
            self.assertEqual((item["start_us"],item["duration_us"]),(5_000_000,1_020_000_000))
        songs=packaging["music"]
        self.assertGreaterEqual(len(songs),2)
        self.assertEqual(len({item["id"] for item in songs}),len(songs))
        self.assertEqual(sum(item["duration_us"] for item in songs),1_020_000_000)
        self.assertLessEqual(songs[-1]["duration_us"],round(songs[-1]["seconds"]*1_000_000))

    def test_auto_native_draft_attaches_five_effects_to_each_product_image(self):
        from mixcut.draft import export_draft
        plan=self.auto_plan()
        snapshot=copy.deepcopy(plan["render"]["packaging_plan"])
        result=export_draft(self.project,plan["id"])
        content=read_json(Path(result["draft_path"])/"draft_content.json")
        self.assertFalse([track for track in content["tracks"] if track["type"]=="effect"])
        materials={item["id"]:item for item in content["materials"]["video_effects"]}
        main=next(track for track in content["tracks"] if track["type"]=="video")
        tail=main["segments"][-4:]
        self.assertEqual(len(tail),4)
        effect_sets={frozenset(materials[ref]["resource_id"] for ref in segment["extra_material_refs"] if ref in materials) for segment in tail}
        self.assertEqual(len(effect_sets),1)
        resources=next(iter(effect_sets))
        self.assertEqual(len(resources),5)
        refs=[ref for segment in tail for ref in segment["extra_material_refs"] if ref in materials]
        self.assertEqual(len(refs),20)
        self.assertTrue(all(materials[ref]["apply_target_type"]==0 for ref in refs))
        self.assertTrue(all(segment["volume"]==0 for segment in tail))
        self.assertTrue(all(not ({ref for ref in segment.get("extra_material_refs",[])} & set(materials)) for segment in main["segments"][:5]))
        audio=next(track for track in content["tracks"] if track["type"]=="audio")["segments"]
        self.assertEqual(len(audio),len(snapshot["music"]))
        for segment,song in zip(audio,snapshot["music"]):
            self.assertEqual(segment["target_timerange"],{"start":song["start_us"],"duration":song["duration_us"]})
            self.assertEqual(segment["source_timerange"],{"start":0,"duration":song["duration_us"]})
        self.project.config["mix"]["random_seed"]+=1
        self.project.save_config()
        self.assertEqual(export_draft(self.project,plan["id"])["draft_path"],result["draft_path"])
        self.assertEqual(read_json(self.project.plans/(plan["id"]+".json"))["render"]["packaging_plan"],snapshot)
        self.assertFalse(result["rendered"])

    def test_real_sdk_two_compounds_keep_five_effects_and_original_audio(self):
        from mixcut.compound_draft import export_batch_draft
        first=self.auto_plan(seconds=1)
        second=make_plans(self.project,1)[0]
        snapshots=[read_json(self.project.plans/(p["id"]+".json")) for p in [first,second]]
        result=export_batch_draft(self.project,[first["id"],second["id"]],str(self.root/"batch"))
        parent=read_json(Path(result["draft_path"])/"draft_content.json")
        self.assertEqual(len(parent["tracks"]),1)
        self.assertEqual(len(parent["tracks"][0]["segments"]),2)
        self.assertEqual(parent["duration"],18_000_000)
        for item in parent["materials"]["drafts"]:
            child=item["draft"]
            main=next(t for t in child["tracks"] if t["type"]=="video")
            self.assertEqual(len(main["segments"]),9)
            self.assertTrue(all(s["volume"]==1 for s in main["segments"][:5]))
            self.assertEqual([s["target_timerange"]["duration"] for s in main["segments"][-4:]],[1_000_000]*4)
            materials={m["id"]:m for m in child["materials"]["video_effects"]}
            for segment in main["segments"][-4:]:
                self.assertEqual(len({materials[ref]["resource_id"] for ref in segment["extra_material_refs"] if ref in materials}),5)
            self.assertEqual(child,read_json(Path(item["draft_file_path"])))
        self.assertEqual(snapshots,[read_json(self.project.plans/(p["id"]+".json")) for p in [first,second]])

    def test_missing_music_repair_preserves_images_effects_and_excludes_future_selection(self):
        from mixcut.music_repair import repair_music
        from mixcut.auto_packaging import frozen_packaging
        first=self.auto_plan(seconds=1)
        second=make_plans(self.project,1)[0]
        bad=first["render"]["packaging_plan"]["music"][0]["id"]
        # Short tails select one song; ensure an independent replacement pool.
        from mixcut.auto_packaging import music_pool
        donor=next(s for s in music_pool() if s["id"]!=bad and s["seconds"]>=4)
        second["render"]["packaging_plan"]["music"]=[{**donor,"source_start_us":0,"start_us":5_000_000,"duration_us":4_000_000}]
        write_json(self.project.plans/(second["id"]+".json"),second)
        before=[p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))]
        with patch("mixcut.music_repair.music_pool",return_value=[]):
            with self.assertRaises(PipelineError):repair_music(self.project,[bad])
        self.assertEqual(before,[p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))])
        report=repair_music(self.project,[bad])
        self.assertEqual(report["count"],1)
        fixed=read_json(self.project.plans/(first["id"]+".json"))
        self.assertEqual(fixed["clips"],first["clips"])
        self.assertEqual(fixed["render"]["product_images"],first["render"]["product_images"])
        self.assertEqual(fixed["render"]["packaging_plan"]["effects"],first["render"]["packaging_plan"]["effects"])
        self.assertEqual(fixed["render"]["packaging_plan"]["seed"],first["render"]["packaging_plan"]["seed"])
        self.assertEqual(read_json(Path(report["backup"])/(first["id"]+".json")),first)
        self.assertFalse({bad}.intersection(s["id"] for s in frozen_packaging(fixed,fixed["render"])["music"]))
        third=make_plans(self.project,1)[0]
        self.assertNotIn(bad,[s["id"] for s in third["render"]["packaging_plan"]["music"]])
        snapshots=[p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))]
        write_json(self.project.state/"render_status.json",{p["id"]:{"status":"complete"} for p in [first,second,third]})
        self.assertEqual(repair_music(self.project,[donor["id"]])["count"],0)
        self.assertEqual(snapshots,[p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))])

    def test_auto_playlist_truncates_only_final_song_and_rejects_boundary_changes(self):
        from mixcut.auto_packaging import frozen_packaging
        from mixcut.jianying_assets import catalog
        songs=[{**item,"seconds":1.5} for item in catalog("music")[:3]]
        with patch("mixcut.auto_packaging.music_pool",return_value=songs):
            plan=self.auto_plan(seconds=1)
        packaging=frozen_packaging(plan,plan["render"])
        self.assertEqual([item["duration_us"] for item in packaging["music"]],[1_500_000,1_500_000,1_000_000])
        altered=copy.deepcopy(plan["render"])
        altered["packaging_plan"]["effects"][0]["start_us"]=0
        with self.assertRaises(PipelineError):frozen_packaging(plan,altered)
        altered=copy.deepcopy(plan["render"])
        altered["packaging_plan"]["music"][1]["start_us"]-=1
        with self.assertRaises(PipelineError):frozen_packaging(plan,altered)

    def test_auto_save_needs_no_manual_music_and_invalid_pool_is_atomic(self):
        values=self.values()
        values.update(output_mode="jianying",jianying={"selection_mode":"auto","music_source":"cloud",
            "cloud_music":{},"effect_categories":["氛围","动感"],"effect_count":5})
        FolderService(self.config).save(values)
        before=self.config.read_bytes()
        values["jianying"]["effect_categories"]=["其它"]
        with self.assertRaises(PipelineError):FolderService(self.config).save(values)
        self.assertEqual(before,self.config.read_bytes())

    def test_legacy_frozen_plan_survives_gentle_default(self):
        from mixcut.auto_packaging import frozen_packaging
        self.auto_plan(seconds=1)
        self.project.config["render"]["jianying"]["effect_profile"]="legacy"
        self.project.save_config()
        legacy=make_plans(self.project,1)[0]
        before=copy.deepcopy(legacy)
        self.project.config["render"]["jianying"]["effect_profile"]="gentle_3_2"
        self.project.save_config()
        frozen_packaging(legacy,legacy["render"])
        self.assertEqual(before,legacy)

    def test_large_pool_draws_three_and_two_with_varying_combinations(self):
        plans=[self.auto_plan(seconds=1),*make_plans(self.project,12)]
        combinations=set()
        for plan in plans:
            effects=plan['render']['packaging_plan']['effects']
            self.assertEqual(sum(e['category']=='氛围' for e in effects),3)
            self.assertEqual(sum(e['category']=='动感' for e in effects),2)
            combinations.add(tuple(sorted(e['resource_id'] for e in effects)))
        self.assertGreater(len(combinations),1)

    def test_native_face_cover_starts_after_opening_and_preserves_tail_five(self):
        from mixcut.draft import export_draft
        plan=self.auto_plan(seconds=1)
        plan["render"]["jianying"]["face_cover_enabled"]=True
        write_json(self.project.plans/(plan["id"]+".json"),plan)
        result=export_draft(self.project,plan["id"])
        content=read_json(Path(result["draft_path"])/"draft_content.json")
        face=next(t for t in content["tracks"] if t.get("name","").startswith("人物挡脸_"))
        self.assertEqual(face["segments"][0]["target_timerange"],{"start":1_000_000,"duration":8_000_000})
        materials={m["id"]:m for m in content["materials"]["video_effects"]}
        self.assertEqual(materials[face["segments"][0]["material_id"]]["resource_id"],"7536096426359770392")
        self.assertEqual([t for t in content["tracks"] if t["type"]=="effect"],[face])
        main=next(t for t in content["tracks"] if t["type"]=="video")
        for segment in main["segments"][-4:]:
            self.assertEqual(len({materials[ref]["resource_id"] for ref in segment["extra_material_refs"] if ref in materials}),5)
        self.assertEqual(content,read_json(Path(result["draft_path"])/"draft_info.json"))
        self.assertIn("拽酷墨镜从 1 秒开始覆盖至结尾",result["note"])
        self.assertIn("不可商用",(Path(result["draft_path"])/"人物墨镜说明.md").read_text(encoding="utf-8"))

    def test_auto_pending_update_rebuilds_tail_without_changing_images(self):
        plan=self.auto_plan()
        before=[item["path"] for item in plan["render"]["product_images"]]
        cfg=read_json(self.config);cfg["folder_mix"]["image_seconds"]=240
        write_json(self.config,cfg)
        service=FolderService(self.config)
        self.assertEqual(service.apply_render_to_pending(),1)
        updated=read_json(self.project.plans/(plan["id"]+".json"))
        self.assertEqual([item["path"] for item in updated["render"]["product_images"]],before)
        self.assertEqual(updated["render"]["packaging_plan"]["duration_us"],960_000_000)
        self.assertEqual(sum(item["duration_us"] for item in updated["render"]["packaging_plan"]["music"]),960_000_000)

    def test_batches_filter_history_and_settings_only_update_current_batch(self):
        from mixcut.plan_batches import get_batch
        first=self.auto_plan(seconds=1)
        previous_batch=get_batch(self.project)
        snapshot=(self.project.plans/(first["id"]+".json")).read_bytes()
        second=make_plans(self.project,1)[0]
        self.assertNotEqual(previous_batch["batch_id"],get_batch(self.project)["batch_id"])
        service=FolderService(self.config)
        self.assertEqual([r["id"] for r in service.plan_rows()],[second["id"]])
        self.assertEqual(len(service.plan_rows(all_history=True)),2)
        cfg=read_json(self.config);cfg["folder_mix"]["image_seconds"]=2;write_json(self.config,cfg)
        self.assertEqual(service.apply_render_to_pending(),1)
        self.assertEqual(snapshot,(self.project.plans/(first["id"]+".json")).read_bytes())
        self.assertEqual(read_json(self.project.plans/(second["id"]+".json"))["render"]["tail_seconds"],8)

    def test_failed_plan_generation_retains_previous_batch(self):
        self.auto_plan(seconds=1)
        pointer=(self.project.state/"current_plan_batch.json").read_bytes()
        before={p.name:p.read_bytes() for p in self.project.plans.glob("*.json")}
        with self.assertRaisesRegex(PipelineError,"没有保存本批计划"):
            make_plans(self.project,25)
        self.assertEqual(pointer,(self.project.state/"current_plan_batch.json").read_bytes())
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.project.plans.glob("*.json")})

    def enable_corners(self):
        self.project.config["render"]["corner_overlay"]={"enabled":True,"folder":"","width_percent":18,"max_height_percent":15}
        self.project.save_config()

    def test_corner_geometry_touches_all_four_edges_without_stretching(self):
        from mixcut.corner_overlay import geometry,POSITIONS
        for iw,ih in [(100,100),(120,400),(500,100)]:
            for position in POSITIONS:
                g=geometry({"width":iw,"height":ih,"position":position},1080,1920,{"width_percent":18,"max_height_percent":15})
                self.assertAlmostEqual(g["width"]/g["height"],iw/ih)
                self.assertLessEqual(g["width"],1080*.18+.001)
                self.assertLessEqual(g["height"],1920*.15+.001)
                left=(g["transform_x"]+1)*1080/2-g["width"]/2
                top=(1-g["transform_y"])*1920/2-g["height"]/2
                self.assertAlmostEqual(left+g["width"] if position.startswith("右") else left,1080 if position.startswith("右") else 0)
                self.assertAlmostEqual(top if position.endswith("上") else top+g["height"],0 if position.endswith("上") else 1920)

    def test_corner_selection_is_frozen_and_modified_image_is_rejected(self):
        from mixcut.corner_overlay import frozen_corners,POSITIONS
        self.enable_corners();plan=make_plans(self.project,1)[0]
        value=frozen_corners(self.project,plan["render"])
        self.assertEqual([i["position"] for i in value["images"]],POSITIONS)
        self.assertEqual(len({i["sha256"] for i in value["images"]}),4)
        Image.new("RGB",(160,284),(20,20,20)).save(Path(settings(self.project)["image_folder"])/"new.png")
        self.assertEqual(value,frozen_corners(self.project,plan["render"]))
        Path(value["images"][0]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(PipelineError,"四角图片被移动或修改"):
            frozen_corners(self.project,plan["render"])

    def test_corner_save_rejects_duplicate_content_and_preserves_config(self):
        folder=self.root/"corner_pool";folder.mkdir()
        images=sorted(Path(settings(self.project)["image_folder"]).glob("*.png"))
        for image in images[:3]:shutil.copy2(image,folder/image.name)
        shutil.copy2(images[0],folder/"duplicate.png")
        values=self.values();values["corner_overlay"]={"enabled":True,"folder":str(folder),"width_percent":18}
        before=self.config.read_bytes()
        with self.assertRaisesRegex(PipelineError,"至少需要 4 张内容不同"):
            FolderService(self.config).save(values)
        self.assertEqual(before,self.config.read_bytes())
        values["corner_overlay"]["width_percent"]="nan"
        with self.assertRaises(PipelineError):FolderService(self.config).save(values)
        self.assertEqual(before,self.config.read_bytes())

    def test_corner_native_draft_keeps_corners_above_segment_effects(self):
        from mixcut.draft import export_draft
        self.enable_corners();plan=self.auto_plan(seconds=1)
        plan["render"]["jianying"]["face_cover_enabled"]=True
        write_json(self.project.plans/(plan["id"]+".json"),plan)
        result=export_draft(self.project,plan["id"])
        content=read_json(Path(result["draft_path"])/"draft_content.json")
        # 一条扁平时间线：无复合、无整轨特效；四角图在最后 4 条轨道（最上层）。
        self.assertFalse(content["materials"].get("drafts"))
        self.assertEqual([t["name"] for t in content["tracks"][-4:]],
            ["四角产品图_左上","四角产品图_右上","四角产品图_左下","四角产品图_右下"])
        self.assertTrue(all(t["type"]=="video" for t in content["tracks"][-4:]))
        for track in content["tracks"][-4:]:
            self.assertEqual(track["segments"][0]["target_timerange"],{"start":0,"duration":9_000_000})
            self.assertEqual(track["segments"][0]["volume"],0)
        # 整轨特效只剩人物墨镜；五层特效挂在四张产品图片段上。
        effect_tracks=[t for t in content["tracks"] if t["type"]=="effect"]
        self.assertEqual([t["name"] for t in effect_tracks],["人物挡脸_拽酷墨镜_跳过开头"])
        materials={m["id"]:m for m in content["materials"]["video_effects"]}
        main=next(t for t in content["tracks"] if t["type"]=="video")
        for segment in main["segments"][-4:]:
            self.assertEqual(len({materials[ref]["resource_id"] for ref in segment["extra_material_refs"] if ref in materials}),5)
        self.assertEqual(result["corner_overlay"]["edge_margin_pixels"],0)
        self.assertEqual(content,read_json(Path(result["draft_path"])/"draft_info.json"))

    def test_corner_batch_builds_one_complete_compound_per_clip(self):
        from mixcut.compound_draft import export_batch_draft
        self.enable_corners();first=self.auto_plan(seconds=1);second=make_plans(self.project,1)[0]
        result=export_batch_draft(self.project,[first["id"],second["id"]],str(self.root/"published_corners"))
        path=Path(result["draft_path"]);parent=read_json(path/"draft_content.json")
        # 一条轨道、两个复合片段：打开即可全选导出，四角图在复合片段内部。
        self.assertEqual(len(parent["tracks"]),1)
        self.assertEqual(len(parent["tracks"][0]["segments"]),2)
        ids=[]
        for outer in parent["materials"]["drafts"]:
            child=read_json(Path(outer["draft_file_path"]))
            self.assertFalse(child["materials"].get("drafts"))
            self.assertEqual(child,outer["draft"])
            self.assertEqual([t["name"] for t in child["tracks"][-4:]],
                ["四角产品图_左上","四角产品图_右上","四角产品图_左下","四角产品图_右下"])
            ids.append(child["id"])
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(all(e["corner_overlay_enabled"] for e in result["clips"]))
        self.assertTrue(result["corner_overlay"]["enabled"])
        self.assertEqual(result["corner_overlay"]["scope"],"inside_each_compound")
        # 已经打开的草稿由剪映改写后仍然复用，不覆盖客户端内容。
        (path/"draft_content.json").write_text("native-client-format",encoding="utf-8")
        self.assertTrue(export_batch_draft(self.project,[first["id"],second["id"]],str(self.root/"published_corners"))["reused"])

    def test_corner_local_mp4_keeps_images_at_frame_edges_from_start_to_end(self):
        self.enable_corners();plan=make_plans(self.project,1)[0]
        result=render_plan(self.project,plan)
        corners=plan["render"]["corner_overlay_plan"]["images"]
        for second in [.2,result["duration"]-.2]:
            frame=self.root/f"frame_{second:g}.png"
            run_command([runtime_tool("ffmpeg"),"-v","error","-nostdin","-y","-ss",str(second),"-i",result["path"],"-frames:v","1",str(frame)])
            with Image.open(frame) as rendered:
                for corner in corners:
                    x=rendered.width-2 if corner["position"].startswith("右") else 1
                    y=1 if corner["position"].endswith("上") else rendered.height-2
                    actual=rendered.convert("RGB").getpixel((x,y))
                    with Image.open(corner["path"]) as source:expected=source.convert("RGB").getpixel((1,1))
                    self.assertTrue(all(abs(a-b)<28 for a,b in zip(actual,expected)),(corner["position"],actual,expected))
        self.assertEqual(result["corner_overlay"]["edge_margin_pixels"],0)

    def test_corner_size_update_preserves_choices_and_only_changes_current_batch(self):
        self.enable_corners();first=make_plans(self.project,1)[0]
        snapshot=(self.project.plans/(first["id"]+".json")).read_bytes()
        second=make_plans(self.project,1)[0]
        before=second["render"]["corner_overlay_plan"]["images"]
        cfg=read_json(self.config);cfg["render"]["corner_overlay"]["width_percent"]=24;write_json(self.config,cfg)
        self.assertEqual(FolderService(self.config).apply_render_to_pending(),1)
        updated=read_json(self.project.plans/(second["id"]+".json"))
        self.assertEqual(updated["render"]["corner_overlay_plan"]["images"],before)
        self.assertEqual(updated["render"]["corner_overlay_plan"]["policy"]["width_percent"],24)
        self.assertEqual(snapshot,(self.project.plans/(first["id"]+".json")).read_bytes())

    def test_legacy_container_overrun_keeps_frozen_choices_and_realigns_tail(self):
        from mixcut.auto_packaging import attach_auto_packaging
        from mixcut.draft import export_draft
        plan=self.auto_plan()
        plan["clips"][0]["duration"]+=.013991
        plan["clips"][0]["end"]+=.013991
        plan["body_seconds"]+=.013991
        attach_auto_packaging(self.project,plan)
        write_json(self.project.plans/(plan["id"]+".json"),plan)
        before=copy.deepcopy(plan)
        result=export_draft(self.project,plan["id"])
        self.assertEqual(result["tail_start_seconds"],5)
        self.assertEqual(result["duration_adjustments"][0]["planned_us"],1_013_991)
        self.assertEqual(result["duration_adjustments"][0]["used_us"],1_000_000)
        self.assertEqual([item["key"] for item in result["native_effects"]],
            [item["key"] for item in before["render"]["packaging_plan"]["effects"]])
        self.assertEqual([item["id"] for item in result["music_playlist"]],
            [item["id"] for item in before["render"]["packaging_plan"]["music"]])
        self.assertTrue(all(item["start_us"]==5_000_000 for item in result["native_effects"]))
        self.assertEqual(result["music_playlist"][0]["start_us"],5_000_000)
        content=read_json(Path(result["draft_path"])/"draft_content.json")
        self.assertTrue(all(item["speed"]==1 for item in content["materials"]["speeds"]))
        self.assertFalse([track for track in content["tracks"] if track["type"]=="effect"])
        materials={m["id"]:m for m in content["materials"]["video_effects"]}
        main=next(track for track in content["tracks"] if track["type"]=="video")
        self.assertEqual(len(main["segments"][-4:]),4)
        for segment in main["segments"][-4:]:
            self.assertEqual(segment["target_timerange"]["start"]>=5_000_000,True)
            self.assertEqual(len({materials[ref]["resource_id"] for ref in segment["extra_material_refs"] if ref in materials}),5)
        self.assertEqual(read_json(self.project.plans/(plan["id"]+".json")),before)

    def test_native_value_error_marks_failed_and_batch_continues(self):
        self.native_plan("none")
        first,second=[p["id"] for p in make_plans(self.project,2)]
        def fake_export(project,identity):
            if identity==first:raise ValueError("test duration mismatch")
            return {"draft_path":str(self.root/"result"),"rendered":False}
        with patch("mixcut.draft.export_draft",side_effect=fake_export):
            with self.assertRaises(PipelineError):render_pending(self.project)
        status=read_json(self.project.state/"render_status.json")
        self.assertEqual(set(status),{first,second})
        self.assertEqual(status[first]["status"],"failed")
        self.assertEqual(status[second]["status"],"draft_ready")
        self.assertEqual(read_json(self.project.state/"batch_report.json")["failed"],1)
        self.assertTrue((self.project.state/"logs"/("draft_"+first+".log")).is_file())

    def test_cloud_music_and_effects_are_strictly_after_body(self):
        from mixcut.draft import export_draft
        plan=self.native_plan("cloud")
        # Export must enforce the boundary even for an old/mistaken scope snapshot.
        plan["render"]["meteor"]["scope"]="all";plan["render"]["original_audio_volume"]=.1
        write_json(self.project.plans/(plan["id"]+".json"),plan)
        result=export_draft(self.project,plan["id"])
        content=self.assert_tail_boundary(plan,result)
        self.assertEqual(content["materials"]["audios"][0]["type"],"music")
        self.assertEqual(content["materials"]["audios"][0]["music_id"],plan["render"]["jianying"]["cloud_music"]["id"])
        metadata=read_json(Path(result["draft_path"])/"draft_meta_info.json")
        self.assertEqual(metadata["draft_id"],content["id"])
        self.assertTrue((Path(result["draft_path"])/"draft_info.json").is_file())
        plan["render"]["music_volume"] = .3
        write_json(self.project.plans/(plan["id"]+".json"),plan)
        second=export_draft(self.project,plan["id"])
        other=read_json(Path(second["draft_path"])/"draft_content.json")
        self.assertNotEqual(content["id"],other["id"])
        self.assertEqual(export_draft(self.project,plan["id"])["draft_path"],second["draft_path"])

    def test_local_music_in_native_draft_loops_only_over_images(self):
        from mixcut.draft import export_draft
        plan=self.native_plan("local")
        result=export_draft(self.project,plan["id"])
        content=self.assert_tail_boundary(plan,result)
        self.assertEqual(content["materials"]["audios"][0]["type"],"extract_music")

    def test_native_queue_reports_draft_ready_not_mp4(self):
        plan=self.native_plan("none")
        result=render_pending(self.project)
        self.assertEqual(result["completed"],1)
        self.assertFalse(result["reports"][0]["rendered"])
        status=read_json(self.project.state/"render_status.json")
        self.assertEqual(status[plan["id"]]["status"],"draft_ready")
        self.assertFalse(list(self.project.outputs.glob("*.mp4")))

    def test_ffmpeg_tail_music_does_not_leak_into_original_audio(self):
        # Exercise a fractional body duration and a different source/output fps.
        first=Path(settings(self.project)["slots"][0]["folder"])/self.sources[0].name
        run_command([runtime_tool("ffmpeg"),"-v","error","-nostdin","-y","-f","lavfi","-i","color=c=red:s=160x284:r=25:d=1.12",
            "-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=1.12","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(first)])
        self.project.config["render"].update(music=str(self.music()),music_volume=.6,original_audio_volume=.2,
            meteor={"enabled":True,"scope":"all","intensity":.55,"seed":101})
        plan=make_plans(self.project,1)[0]
        report=render_plan(self.project,plan)
        self.assertGreaterEqual(report["music_start_seconds"],report["tail_start_seconds"])
        self.assertAlmostEqual(report["music_start_seconds"],report["tail_start_seconds"],delta=1/48000)
        def tone(path,start,frequency,seconds=.5):
            result=subprocess.run([runtime_tool("ffmpeg"),"-v","error","-nostdin","-ss",str(start),"-i",str(path),"-t",str(seconds),"-vn","-ac","1","-ar","48000","-f","f32le","-"],capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            samples=struct.unpack('<'+'f'*(len(result.stdout)//4),result.stdout)
            real=sum(value*math.cos(2*math.pi*frequency*i/48000) for i,value in enumerate(samples))
            imaginary=sum(value*math.sin(2*math.pi*frequency*i/48000) for i,value in enumerate(samples))
            return 2*math.hypot(real,imaginary)/len(samples)
        body_bgm=tone(report["path"],.2,880)
        tail_bgm=tone(report["path"],report["music_start_seconds"]+.2,880)
        self.assertGreater(tail_bgm,.05)
        self.assertLess(body_bgm,tail_bgm/20)
        self.assertLess(tone(report["path"],report["tail_start_seconds"]-.035,880,.015),tail_bgm/20)
        original=tone(plan["clips"][0]["path"],.2,440)
        rendered=tone(report["path"],.2,440)
        self.assertAlmostEqual(rendered,original,delta=.015)

    def test_catalog_contains_metadata_only_and_invalid_native_save_is_atomic(self):
        from mixcut.jianying_assets import catalog
        self.assertGreater(len(catalog("music")),100)
        self.assertTrue(all(set(item)=={"id","name","seconds","category"} for item in catalog("music")))
        self.assertGreater(len(catalog("effects")),100)
        before=self.config.read_bytes();values=self.values()
        values.update(output_mode="jianying",jianying={"music_source":"cloud","cloud_music":{"id":"invalid","name":"x","seconds":1},"effect_enabled":True,"effect_key":"流星雨"})
        with self.assertRaises(PipelineError):FolderService(self.config).save(values)
        self.assertEqual(before,self.config.read_bytes())

    def test_ordered_folders_distinct_images_no_ai_and_no_sidecars(self):
        cfg=self.project.config["folder_mix"]
        image_folder=Path(cfg["image_folder"])
        shutil.copy2(image_folder/"0.png",image_folder/"same-picture-renamed.png")
        before={str(path) for path in self.project.path.rglob("*") if path.is_file()}
        with patch("mixcut.analyze.request_json",side_effect=AssertionError("No AI in folder mode")) as api:
            plans=make_plans(self.project,3)
            self.assertEqual(api.call_count,0)
        self.assertEqual(len({plan["signature"] for plan in plans}),3)
        for plan in plans:
            self.assertEqual([clip["label"] for clip in plan["clips"]],[slot["label"] for slot in cfg["slots"]])
            self.assertEqual(len({image["sha256"] for image in plan["render"]["product_images"]}),4)
            self.assertEqual(plan["render"]["tail_seconds"],4)
        self.assertFalse(list(self.project.path.rglob("*.clip.json")))

    def test_exact_capacity_and_batch_failure_is_atomic(self):
        self.assertEqual(len(make_plans(self.project,24)),24)
        with self.assertRaises(PipelineError):make_plans(self.project,1)
        self.assertEqual(len(list(self.project.plans.glob("*.json"))),24)

    def test_real_render_has_five_stages_four_images_in_order_and_resume(self):
        plan=make_plans(self.project,1)[0]
        report=render_pending(self.project)["reports"][0]
        self.assertAlmostEqual(report["duration"],9,delta=.25)
        self.assertTrue(report["full_decode"])
        self.assertEqual(len(report["tail_images"]),4)
        frames=self.root/"frames";frames.mkdir()
        run_command([runtime_tool("ffmpeg"),"-v","error","-nostdin","-i",report["path"],"-vf","fps=1","-frames:v","9",str(frames/"%02d.png")])
        for index,item in enumerate(plan["render"]["product_images"],6):
            expected=Image.open(item["path"]).convert("RGB").getpixel((80,142))
            actual=Image.open(frames/f"{index:02d}.png").convert("RGB").getpixel((80,142))
            self.assertLess(max(abs(a-b) for a,b in zip(expected,actual)),15)
        self.assertTrue(render_pending(self.project)["reports"][0]["reused"])
        Path(plan["clips"][0]["path"]).rename(self.root/"moved-original.mp4")
        self.assertTrue(render_pending(self.project)["reports"][0]["reused"])

    def test_changed_picture_blocks_old_plan(self):
        plan=make_plans(self.project,1)[0]
        Path(plan["render"]["product_images"][0]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(PipelineError,"商品图被修改"):render_plan(self.project,plan)

    def test_native_draft_contains_four_image_segments(self):
        from mixcut.draft import export_draft
        plan=make_plans(self.project,1)[0]
        result=export_draft(self.project,plan["id"])
        draft=read_json(Path(result["draft_path"])/"draft_content.json")
        self.assertEqual(draft["duration"],9_000_000)
        self.assertEqual(sum(item.get("type")=="photo" for item in draft["materials"]["videos"]),4)
        main=next(track for track in draft["tracks"] if track["type"]=="video")
        self.assertEqual(len(main["segments"]),9)
        self.assertEqual([segment["target_timerange"]["duration"] for segment in main["segments"][-4:]],[1_000_000]*4)

    def test_reorder_save_and_pending_packaging_keep_selected_images(self):
        service=FolderService(self.config);values=self.values()
        values["slots"]=list(reversed(values["slots"]))
        service.save(values);self.project=service.project
        plans=make_plans(self.project,2)
        self.assertEqual(plans[0]["clips"][0]["label"],values["slots"][0]["label"])
        write_json(self.project.state/"render_status.json",{plans[1]["id"]:{"status":"complete"}})
        original_images=plans[0]["render"]["product_images"]
        values["image_seconds"]=5;service.save(values)
        self.assertEqual(service.apply_render_to_pending(),1)
        changed=read_json(self.project.plans/(plans[0]["id"]+".json"))
        self.assertEqual(changed["render"]["tail_seconds"],20)
        self.assertEqual([item["path"] for item in changed["render"]["product_images"]],[item["path"] for item in original_images])
        unchanged=read_json(self.project.plans/(plans[1]["id"]+".json"))
        self.assertEqual(unchanged["render"]["tail_seconds"],4)

    def test_invalid_settings_do_not_change_saved_config(self):
        before=self.config.read_bytes();values=self.values();values["slots"][0]["folder"]="missing-folder"
        with self.assertRaises(PipelineError):FolderService(self.config).save(values)
        self.assertEqual(before,self.config.read_bytes())

    def test_repeating_same_video_across_stages_is_blocked(self):
        self.project.config["folder_mix"]["slots"][1]["folder"]=self.project.config["folder_mix"]["slots"][0]["folder"]
        with self.assertRaises(PipelineError):make_plans(self.project,1)
        self.assertFalse(list(self.project.plans.glob("*.json")))
