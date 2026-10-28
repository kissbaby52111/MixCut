from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

from mixcut.common import ROOT, PipelineError, read_json, write_json
from mixcut.ui_service import UiService, JobRunner

class UiServiceTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / ".runtime/gui-tests"
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.tmp.name)
        config = copy.deepcopy(read_json(ROOT / "config.example.json"))
        config["workspace"] = "work"
        config["workflow"] = "ai_pipeline"
        config["analysis"]["api_key_env"] = "MIXCUT_TEST_VISION"
        config["analysis"]["transcription"]["api_key_env"] = "MIXCUT_TEST_ASR"
        self.config_path = self.root / "config.json"
        write_json(self.config_path, config)
        self.env_path = self.root / ".env"
        self.service = UiService(self.config_path, self.env_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_switch_product_isolates_files_and_resets_product_assets(self):
        old = self.service.project
        (old.inbox / "original.mp4").write_bytes(b"preserve")
        old.config["render"].update({"product_image":"old.png","music":"old.wav"})
        old.save_config()
        self.assertTrue(self.service.save_basic("另一个商品", 5, 2))
        self.assertNotEqual(old.path, self.service.project.path)
        self.assertEqual((old.inbox / "original.mp4").read_bytes(), b"preserve")
        self.assertEqual(self.service.project.config["render"]["product_image"], "")
        self.assertEqual(self.service.project.config["mix"]["count"], 2)

    def test_review_move_preserves_sidecars_and_can_be_restored(self):
        project = self.service.project
        source = project.inbox / "raw.mp4"
        source.write_bytes(b"video")
        write_json(source.with_suffix(".source.json"), {"path":str(source),"id":"same-id"})
        source.with_suffix(".srt").write_text("subtitles",encoding="utf-8")
        write_json(project.state / "sources.json", {"hash": {"path":str(source)}})
        self.service.move_materials([source], "已通过")
        approved = project.approved / source.name
        self.assertTrue(approved.exists())
        self.assertEqual(read_json(approved.with_suffix(".source.json"))["id"], "same-id")
        self.assertTrue(approved.with_suffix(".srt").exists())
        self.service.move_materials([approved], "已淘汰")
        rejected = self.service.rejected / source.name
        self.assertTrue(rejected.exists())
        self.service.move_materials([rejected], "待审核")
        self.assertEqual(source.read_bytes(), b"video")
        self.assertEqual(read_json(project.state / "sources.json")["hash"]["path"], str(source))

    def test_move_collision_does_not_overwrite_or_partially_move(self):
        project = self.service.project
        first, second = project.inbox / "one.mp4", project.inbox / "two.mp4"
        first.write_bytes(b"one"); second.write_bytes(b"two")
        (project.approved / second.name).write_bytes(b"original")
        with self.assertRaisesRegex(PipelineError,"同名"):
            self.service.move_materials([first,second],"已通过")
        self.assertTrue(first.exists())
        self.assertEqual((project.approved / second.name).read_bytes(),b"original")

    def test_clip_category_move_relinks_plan_without_changing_signature(self):
        project = self.service.project
        source = project.clips / "待确认/test.mp4"
        source.write_bytes(b"clip")
        write_json(source.with_suffix(".clip.json"),{"id":"clip-id","approved":False,"path":str(source)})
        plan = {"id":"a"*16,"signature":"unchanged","clips":[{"path":str(source),"file_sha256":"expected"}]}
        plan_path = project.plans / "test.json"
        write_json(plan_path,plan)
        self.service.move_clips([source],"效果展示")
        target = project.clips / "效果展示/test.mp4"
        self.assertTrue(read_json(target.with_suffix(".clip.json"))["approved"])
        saved = read_json(plan_path)
        self.assertEqual(saved["clips"][0]["path"],str(target))
        self.assertEqual(saved["signature"],"unchanged")
        self.assertEqual(saved["clips"][0]["file_sha256"],"expected")

    def test_ai_key_is_only_in_env_and_blank_keeps_it(self):
        self.env_path.write_text("# keep comment\nOTHER_SETTING=value\n",encoding="utf-8")
        with patch.dict(os.environ,{},clear=False):
            self.service.save_ai(base_url="http://127.0.0.1:9999/v1",model="test-vision",api_key="test-not-a-real-secret")
            self.assertNotIn("test-not-a-real-secret",self.config_path.read_text(encoding="utf-8"))
            self.assertIn("# keep comment",self.env_path.read_text())
            self.service.save_ai(base_url="http://127.0.0.1:9999/v1",model="test-vision",api_key="")
            self.assertIn("test-not-a-real-secret",self.env_path.read_text())
            self.assertIn("test-not-a-real-secret",self.service.secret_values())
        before=self.config_path.read_text(encoding="utf-8")
        with self.assertRaises(PipelineError):
            self.service.save_ai(base_url="http://user:password@example.com",model="test")
        self.assertEqual(before,self.config_path.read_text(encoding="utf-8"))

    def test_invalid_render_settings_do_not_change_config(self):
        before=self.config_path.read_text(encoding="utf-8")
        with self.assertRaises(PipelineError):
            self.service.save_render({"width":721,"height":1280,"fps":25,"tail_seconds":300})
        self.assertEqual(before,self.config_path.read_text(encoding="utf-8"))

    def test_packaging_update_skips_completed_plans(self):
        project=self.service.project
        for name in ["pending","complete"]:
            write_json(project.plans/(name+".json"),{"id":name,"render":{"tail_seconds":10},"signature":name})
        write_json(project.state/"render_status.json",{"complete":{"status":"complete"}})
        self.assertEqual(self.service.apply_render_to_pending(),1)
        self.assertEqual(read_json(project.plans/"pending.json")["render"]["tail_seconds"],300)
        self.assertEqual(read_json(project.plans/"complete.json")["render"]["tail_seconds"],10)

class JobRunnerTests(unittest.TestCase):
    def test_background_task_redacts_logs_and_can_be_stopped(self):
        base=ROOT/".runtime/gui-tests"; base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root=Path(directory)
            (root/"run.py").write_text("import time\nprint('ready test-not-a-real-secret', flush=True)\ntime.sleep(30)\n",encoding="utf-8")
            config=root/"config.json"; config.write_text("{}")
            runner=JobRunner(config,secrets=["test-not-a-real-secret"],root=root)
            runner.start("test",[["wait"]])
            events=[]
            deadline=time.monotonic()+8
            try:
                while time.monotonic()<deadline:
                    event=runner.events.get(timeout=3); events.append(event)
                    if event[0]=="log": break
                self.assertTrue(runner.busy)
                with self.assertRaises(PipelineError): runner.start("duplicate",[["wait"]])
                runner.stop(); runner.thread.join(timeout=12)
                self.assertFalse(runner.thread.is_alive())
                while not runner.events.empty(): events.append(runner.events.get_nowait())
                self.assertNotIn("test-not-a-real-secret",json.dumps(events))
                self.assertTrue(any(kind=="done" and value["cancelled"] for kind,value in events))
            finally:
                if runner.busy: runner.stop(); runner.thread.join(timeout=12)

    def test_failed_step_does_not_start_next_step(self):
        base=ROOT/".runtime/gui-tests"; base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root=Path(directory)
            (root/"run.py").write_text("import sys,pathlib\nif sys.argv[-1]=='fail': sys.exit(2)\npathlib.Path('should-not-exist').write_text('bad')\n",encoding="utf-8")
            config=root/"config.json"; config.write_text("{}")
            runner=JobRunner(config,root=root)
            runner.start("test",[["fail"],["next"]]); runner.thread.join(timeout=8)
            self.assertFalse(runner.busy)
            self.assertFalse((root/"should-not-exist").exists())

if __name__=="__main__": unittest.main()
