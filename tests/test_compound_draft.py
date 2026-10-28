from __future__ import annotations
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mixcut.common import ROOT, Project, PipelineError, read_json, write_json
from mixcut.compound_draft import export_batch_draft, isolated_child, flat_content, verify_batch
from mixcut.plan_batches import record_batch


class CompoundDraftTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".runtime/compound-tests"
        base.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.tmp.name)
        config = copy.deepcopy(read_json(ROOT / "config.example.json"))
        config.update(workspace="work", workflow="folder_mix")
        config["render"]["jianying"]["draft_root"] = str(self.root / "published")
        write_json(self.root / "config.json", config)
        self.project = Project(self.root / "config.json")
        self.plans = []
        for identity in ["1" * 16, "2" * 16]:
            plan = {"id": identity, "mode": "folder_mix", "render": {"output_mode": "jianying", "width": 360, "height": 640, "fps": 30}}
            self.plans.append(plan)
            write_json(self.project.plans / f"{identity}.json", plan)
        record_batch(self.project,[p["id"] for p in self.plans])
        self.children = []

    def tearDown(self):
        self.tmp.cleanup()

    def child_export(self, project, identity, output):
        # Deliberately reuse the same source identities across both children.
        # This mimics templates/shared SDK material definitions.
        path = Path(output) / identity
        content = read_json(ROOT / "vendor/pyJianYingDraft/pyJianYingDraft/assets/draft_content_template.json")
        content.update(id="A" * 32, duration=9_000_000, name=identity)
        content["materials"]["videos"] = [{"id": "B" * 32, "type": "video", "path": "D:/media/clip.mp4", "resource_id": "123456789"}]
        main = {"id": "C" * 32, "type": "video", "segments": [{"id": "D" * 32, "material_id": "B" * 32, "volume": 1,
            "target_timerange": {"start": 0, "duration": 5_000_000}, "source_timerange": {"start": 0, "duration": 5_000_000}}]}
        content["tracks"] = [main, {"id": "E" * 32, "type": "audio", "segments": [{"id": "F" * 32,
            "target_timerange": {"start": 5_000_000, "duration": 4_000_000}, "source_timerange": {"start": 0, "duration": 4_000_000}}]}]
        content["tracks"] += [{"id": f"{n:032x}", "type": "effect", "segments": [{"target_timerange": {"start": 5_000_000, "duration": 4_000_000}}]} for n in range(1,6)]
        content["canvas_config"].update(width=360, height=640)
        write_json(path / "draft_content.json", content)
        write_json(path / "draft_meta_info.json", {})
        self.children.append(copy.deepcopy(content))
        return {"draft_path": str(path), "body_seconds": 5, "tail_seconds": 4, "native_effects": [{}]*5, "music_playlist": [{}]*2}

    def build(self, **kwargs):
        with patch("mixcut.compound_draft.export_draft", side_effect=self.child_export):
            return export_batch_draft(self.project, **kwargs)

    def test_compounds_isolate_all_child_tracks_and_keep_inner_timing(self):
        before = [path.read_bytes() for path in sorted(self.project.plans.glob("*.json"))]
        result = self.build()
        path = Path(result["draft_path"])
        content = read_json(path / "draft_content.json")
        self.assertEqual(content, read_json(path / "draft_info.json"))
        self.assertEqual(len(content["tracks"]), 1)
        segments = content["tracks"][0]["segments"]
        self.assertEqual([s["target_timerange"] for s in segments], [{"start": 0, "duration": 9_000_000}, {"start": 9_000_000, "duration": 9_000_000}])
        self.assertEqual(content["duration"], 18_000_000)
        self.assertEqual(len(content["materials"]["drafts"]), 2)
        ids = []
        for segment, video, combined, entry in zip(segments, content["materials"]["videos"], content["materials"]["drafts"], result["clips"]):
            self.assertEqual(segment["material_id"], video["id"])
            self.assertIn(combined["id"], segment["extra_material_refs"])
            self.assertEqual((video["path"], video["extra_type_option"]), ("", 2))
            self.assertEqual(segment["volume"], 1)
            self.assertEqual(segment["source_timerange"], {"start": 0, "duration": 9_000_000})
            child = combined["draft"]
            self.assertEqual(child, read_json(Path(combined["draft_file_path"])))
            self.assertTrue(Path(combined["draft_config_path"]).is_file())
            self.assertEqual(child["tracks"][0]["segments"][0]["material_id"], child["materials"]["videos"][0]["id"])
            self.assertEqual(len([t for t in child["tracks"] if t["type"] == "effect"]), 5)
            self.assertEqual(child["tracks"][1]["segments"][0]["target_timerange"], {"start": 5_000_000, "duration": 4_000_000})
            self.assertEqual(child["materials"]["videos"][0]["resource_id"], "123456789")
            self.assertEqual(child["materials"]["videos"][0]["path"], "D:/media/clip.mp4")
            ids.extend([child["id"], *[t["id"] for t in child["tracks"]], child["materials"]["videos"][0]["id"]])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(result["rendered"])
        self.assertEqual(before, [p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))])
        self.assertEqual(self.build()["draft_path"], result["draft_path"])
        self.assertTrue(self.build()["reused"])

    def test_edited_published_parent_is_preserved_on_repeat(self):
        result = self.build()
        path = Path(result["draft_path"]) / "draft_content.json"
        path.write_text("client-encrypted-draft", encoding="utf-8")
        self.assertTrue(self.build()["reused"])
        self.assertEqual(path.read_text(), "client-encrypted-draft")

    def test_incomplete_existing_batch_is_not_silently_reused(self):
        result = self.build()
        child = Path(result["draft_path"]) / "subdraft" / result["clips"][0]["folder_id"] / "draft_content.json"
        child.unlink()
        with self.assertRaisesRegex(PipelineError, "缺少子草稿"):
            self.build()

    def test_mixed_formats_duplicate_or_missing_plans_fail_before_export(self):
        with patch("mixcut.compound_draft.export_draft") as export:
            with self.assertRaises(PipelineError):
                export_batch_draft(self.project, ["1"*16, "1"*16])
            with self.assertRaises(PipelineError):
                export_batch_draft(self.project, ["3"*16])
            plan = self.plans[1];plan["render"]["fps"] = 60
            write_json(self.project.plans / (plan["id"]+".json"), plan)
            with self.assertRaisesRegex(PipelineError, "相同画幅和帧率"):
                export_batch_draft(self.project)
            export.assert_not_called()

    def test_failed_staging_leaves_no_partial_published_draft(self):
        with patch("mixcut.compound_draft.verify_batch", side_effect=PipelineError("simulated disk error")):
            with self.assertRaises(PipelineError):
                self.build()
        self.assertEqual(list((self.root / "published").iterdir()), [])

    def test_new_batch_exports_only_new_plans_and_keeps_history(self):
        original=[p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))]
        previous=self.build()
        plan=copy.deepcopy(self.plans[0]);plan["id"]="3"*16
        write_json(self.project.plans/(plan["id"]+".json"),plan)
        batch=record_batch(self.project,[plan["id"]])
        current=self.build()
        self.assertEqual(current["count"],1)
        self.assertEqual(current["plan_ids"],["3"*16])
        self.assertEqual(current["plan_batch_id"],batch["batch_id"])
        self.assertIn("1条_复合",Path(current["draft_path"]).name)
        self.assertTrue(Path(previous["draft_path"]).is_dir())
        self.assertEqual(original,[p.read_bytes() for p in sorted(self.project.plans.glob("*.json"))][:2])
        self.assertEqual(self.build(all_plans=True)["count"],3)
        self.assertEqual(self.build(plan_ids=["2"*16])["plan_ids"],["2"*16])

    def test_missing_batch_never_silently_exports_history(self):
        (self.project.state/"current_plan_batch.json").unlink()
        with patch("mixcut.compound_draft.export_draft") as export:
            with self.assertRaisesRegex(PipelineError,"尚无当前批次"):
                export_batch_draft(self.project)
            export.assert_not_called()

    def test_resume_and_missing_plan_are_scoped_before_mutation(self):
        from mixcut.render import render_pending
        from mixcut.plan_batches import scoped_paths
        record_batch(self.project,["2"*16])
        with patch("mixcut.draft.export_draft",return_value={"draft_path":"test-result"}) as export:
            result=render_pending(self.project)
            export.assert_called_once_with(self.project,"2"*16)
        self.assertEqual(result["completed"],1)
        self.assertEqual(set(read_json(self.project.state/"render_status.json")),{"2"*16})
        (self.project.plans/("2"*16+".json")).unlink()
        with self.assertRaisesRegex(PipelineError,"计划不存在"):
            scoped_paths(self.project)


    def corner_child_export(self, project, identity, output):
        """Mimic a corner-wrapped source draft: real timeline sits one level down."""
        result = self.child_export(project, identity, output)
        path = Path(result["draft_path"])
        content = read_json(path / "draft_content.json")
        inner = copy.deepcopy(content)
        inner["id"] = "9" * 32
        wrapper = copy.deepcopy(content)
        wrapper.update(id="7" * 32, name="内容（含音乐、五层特效及人物墨镜）")
        wrapper["tracks"] = [{"id": "6" * 32, "type": "video", "segments": []}]
        wrapper["materials"] = {key: [] for key in content["materials"]}
        wrapper["materials"]["drafts"] = [{"id": "8" * 32, "type": "combination", "combination_type": "none",
            "draft": inner, "draft_file_path": str(path / "subdraft" / ("9" * 32) / "draft_content.json")}]
        write_json(path / "draft_content.json", wrapper)
        image = {"path": str(ROOT / "examples/demo_720x1280_300s/test_product.png"), "sha256": "a" * 64,
                 "width": 720, "height": 1280, "geometry": {"scale": 0.2, "transform_x": -0.4, "transform_y": 0.4}}
        result["corner_overlay"] = {"enabled": True, "scope": "entire_output",
            "images": [dict(image, position=name) for name in ["左上", "右上", "左下", "右下"]]}
        return result

    def test_flat_content_unwraps_corner_wrapper(self):
        inner = {"id": "1" * 32, "duration": 5_000_000, "materials": {"videos": []}}
        wrapper = {"id": "2" * 32, "duration": 5_000_000, "materials": {"drafts": [{"draft": inner}]}}
        self.assertIs(flat_content(wrapper), inner)
        self.assertIs(flat_content(inner), inner)

    def test_corner_wrapper_child_is_flat_and_keeps_one_track(self):
        with patch("mixcut.compound_draft.export_draft", side_effect=self.corner_child_export):
            result = export_batch_draft(self.project, plan_ids=["1" * 16])
        path = Path(result["draft_path"])
        content = read_json(path / "draft_content.json")
        # 一条复合轨，一个片段：复合片段内部就是完整成片，外层不再叠四角轨。
        self.assertEqual(len(content["tracks"]), 1)
        self.assertEqual(len(content["materials"]["drafts"]), 1)
        self.assertTrue(result["corner_overlay"]["enabled"])
        self.assertEqual(result["corner_overlay"]["scope"], "inside_each_compound")
        entry = result["clips"][0]
        self.assertTrue(entry["corner_overlay_enabled"])
        child = read_json(path / "subdraft" / entry["folder_id"] / "draft_content.json")
        self.assertFalse(child.get("materials", {}).get("drafts"))
        self.assertEqual(child["materials"]["videos"][0]["path"], "D:/media/clip.mp4")
        verify_batch(path, result)

    def test_nested_child_draft_is_rejected(self):
        result = self.build(plan_ids=["1" * 16])
        path = Path(result["draft_path"])
        child_path = path / "subdraft" / result["clips"][0]["folder_id"] / "draft_content.json"
        child = read_json(child_path)
        child["materials"]["drafts"] = [{"id": "8" * 32, "draft": {"id": "9" * 32, "duration": 1}}]
        write_json(child_path, child)
        with self.assertRaisesRegex(PipelineError, "套着复合片段"):
            verify_batch(path, result)
