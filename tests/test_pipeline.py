from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import struct
import tempfile
import threading
import time
import subprocess
import sys
import shutil
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import wave

from mixcut.common import ROOT, Project, PipelineError, CommandError, ROLES, read_json, write_json, run_command, runtime_tool
from mixcut.analyze import candidate_ranges, parse_srt, classify, export_clip, analyze
from mixcut.collect import save_local_video, approve, extract_json, collect
from mixcut.planning import combination_signature, valid_combination, make_plans
from mixcut.render import render_pending
from mixcut.cli import project_lock

class CoreTests(unittest.TestCase):
    def test_sentence_boundaries_outweigh_shot_and_duration_target(self):
        speech = [{"start": 0, "end": 5, "text": "一句完整台词"}, {"start": 5, "end": 14, "text": "完整演示"}]
        self.assertEqual(candidate_ranges(14, [2, 3, 7], speech, 2, 8), [(0, 5), (5, 14)])

    def test_long_visual_shot_is_split_and_short_remainder_merged(self):
        ranges = candidate_ranges(25, [], [], 2, 10)
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], 25)
        self.assertTrue(all(2 <= b-a <= 10 for a,b in ranges))

    def test_srt_keeps_chinese_and_timestamps(self):
        value = parse_srt("1\n00:00:01,200 --> 00:00:03,500\n双头染发膏\n\n2\n00:00:04,000 --> 00:00:05,000\n使用展示\n")
        self.assertEqual(value[0], {"start": 1.2, "end": 3.5, "text": "双头染发膏"})

    def test_dedup_ignores_cosmetic_role_and_detects_overlapping_source(self):
        a = {"source_sha256": "same", "start": 0., "end": 3., "role": "hooks"}
        b = {**a, "role": "proof"}
        self.assertEqual(combination_signature([a]), combination_signature([b]))
        self.assertFalse(valid_combination([a, {**a, "start": 2, "end": 5}], 0))
        self.assertTrue(valid_combination([a, {**a, "start": 3.1, "end": 5}], 2))

    def test_cli_json_tolerates_diagnostic_prefix(self):
        self.assertEqual(extract_json('diagnostic\n{"cards":[{"video_id":"123456789012"}]}\n'), {"cards": [{"video_id": "123456789012"}]})

    def test_timeout_does_not_hang_on_inherited_daemon_output(self):
        code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)']); time.sleep(5)"
        start = time.monotonic()
        with self.assertRaisesRegex(PipelineError, "超时"):
            run_command([sys.executable, "-c", code], timeout=.2)
        self.assertLess(time.monotonic()-start, 1.8)

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            runtime_tool("ffmpeg")
            runtime_tool("ffprobe")
        except PipelineError:
            raise unittest.SkipTest("portable ffmpeg/ffprobe not installed")

    def setUp(self):
        base = ROOT / ".runtime/test-work"
        base.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.tmp.name)
        cfg = copy.deepcopy(read_json(ROOT / "config.example.json"))
        cfg["workspace"] = "work"
        cfg["workflow"] = "ai_pipeline"
        # Local tests must stay independent of the user's configured cloud service.
        cfg["analysis"].update({"base_url": "", "model": "", "api_key_env": "MIXCUT_TEST_VISION"})
        cfg["analysis"]["transcription"].update({"base_url": "", "api_key_env": "MIXCUT_TEST_ASR"})
        cfg["render"].update({"width": 160, "height": 284, "fps": 12, "tail_seconds": 2, "product_image": "product.png", "music": "music.wav", "preset": "ultrafast"})
        cfg["mix"]["count"] = 1
        write_json(self.root / "config.json", cfg)
        self.project = Project(self.root / "config.json")
        from PIL import Image
        Image.new("RGB", (160, 284), "#283b63").save(self.root / "product.png")
        with wave.open(str(self.root / "music.wav"), "wb") as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(48000)
            data = bytearray()
            for i in range(48000):
                value = int(5000*math.sin(i/48000*2*math.pi*330))
                data.extend(struct.pack("<hh", value, value))
            out.writeframes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, name, color="red", audio=True):
        path = self.root / name
        cmd = [runtime_tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=160x284:r=12:d=1"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if audio:
            cmd += ["-c:a", "aac", "-shortest"]
        run_command(cmd + [str(path)])
        return path

    def test_manual_review_plan_render_resume_and_capacity(self):
        for role, color in zip(self.project.config["mix"]["structure"], ["red", "green", "blue", "yellow"]):
            source = self.source(f"{role}.mp4", color, audio=role != "proof")
            item = save_local_video(self.project, source)
            self.assertFalse(item.get("duplicate", False))
            self.assertTrue(save_local_video(self.project, source)["duplicate"])
            approve(self.project, [Path(item["path"]).name])
            approved = self.project.approved / Path(item["path"]).name
            export_clip(self.project, approved, 0, 1, role, manual=True)
        plans = make_plans(self.project, 1)
        report = render_pending(self.project)
        self.assertEqual(report["completed"], 1)
        output = report["reports"][0]
        self.assertTrue(output["full_decode"])
        self.assertAlmostEqual(output["duration"], 6, delta=.5)
        self.assertTrue(Path(output["path"]).exists())
        rerun = render_pending(self.project)
        self.assertTrue(rerun["reports"][0]["reused"])
        with self.assertRaisesRegex(PipelineError, "有效组合"):
            make_plans(self.project, 1)
        self.assertEqual(len(list(self.project.plans.glob("*.json"))), 1)
        with project_lock(self.project):
            with self.assertRaises(PipelineError):
                with project_lock(self.project):
                    pass

    def test_ai_protocol_and_visual_only_requires_review(self):
        source = self.source("ai.mp4")
        imported = save_local_video(self.project, source)
        approve(self.project, [Path(imported["path"]).name])
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(data)
                content = json.dumps({"role": "proof", "confidence": .95, "product_match": True, "description": "MOCK TEST ONLY", "reason": "本地协议测试"})
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.project.config["analysis"].update({"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "mock-protocol-test", "min_clip_seconds": .5})
        try:
            report = analyze(self.project)
            self.assertEqual(len(requests), 1)
            image = requests[0]["messages"][1]["content"][1]["image_url"]["url"]
            self.assertTrue(image.startswith("data:image/jpeg;base64,"))
            self.assertFalse(report["processed"][0]["approved"])
            self.assertIn("待确认", report["processed"][0]["path"])
            analyze(self.project)
            self.assertEqual(len(requests), 1, "AI cache should avoid repeat API calls")
            pending = Path(report["processed"][0]["path"])
            approved = self.project.clips / ROLES["proof"] / pending.name
            pending.rename(approved)
            pending.with_suffix(".clip.json").rename(approved.with_suffix(".clip.json"))
            third = analyze(self.project)
            self.assertTrue(third["processed"][0]["approved"])
            self.assertEqual(len(list((self.project.clips / "待确认").glob("*.mp4"))), 0)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_missing_ai_is_explicit_failure(self):
        with self.assertRaisesRegex(PipelineError, "尚未配置"):
            analyze(self.project)

    def test_tampered_clip_is_not_silently_rendered(self):
        from mixcut.render import render_plan
        for role, color in zip(self.project.config["mix"]["structure"], ["red", "green", "blue", "yellow"]):
            export_clip(self.project, self.source(role+".mp4", color), 0, 1, role, manual=True)
        plan = make_plans(self.project, 1)[0]
        with Path(plan["clips"][0]["path"]).open("ab") as file:
            file.write(b"modified-after-planning")
        with self.assertRaisesRegex(PipelineError, "移动或修改"):
            render_plan(self.project, plan)

    def test_native_draft_contains_long_image_music_and_effect(self):
        sys.path.insert(0, str(ROOT / ".runtime/packages"))
        try:
            import pymediainfo
            if not hasattr(pymediainfo, "MediaInfo"):
                self.skipTest("optional draft dependencies not readable in restricted process")
        except ImportError:
            self.skipTest("optional draft dependencies not installed")
        from mixcut.draft import export_draft
        self.project.config["render"]["meteor"] = {"enabled": True, "scope": "all", "intensity": .55, "seed": 101}
        for role, color in zip(self.project.config["mix"]["structure"], ["red", "green", "blue", "yellow"]):
            export_clip(self.project, self.source(role+".mp4", color), 0, 1, role, manual=True)
        plan = make_plans(self.project, 1)[0]
        report = export_draft(self.project, plan["id"])
        content = read_json(Path(report["draft_path"]) / "draft_content.json")
        self.assertEqual(content["duration"], 6_000_000)
        self.assertIn("6974750495415996936", json.dumps(content))
        self.assertTrue(any(v.get("type") == "photo" for v in content["materials"]["videos"]))

    def test_collector_saves_file_and_respects_download_history(self):
        source = self.source("collector.mp4")
        downloaded = self.root / ".runtime/socai-home/runs/test/video.mp4"
        downloaded.parent.mkdir(parents=True)
        shutil.copyfile(source, downloaded)
        responses = [json.dumps({"cards": [{"video_id": "1234567890123456789"}]}), json.dumps({"videos": [{"video_id": "1234567890123456789", "video": {"local_path": str(downloaded)}}]})]
        def command(args, **kwargs):
            import subprocess
            return subprocess.CompletedProcess(args, 0, responses.pop(0), "")
        with patch("mixcut.collect.ROOT", self.root), patch("mixcut.collect.runtime_tool", return_value="socai.exe"), patch("mixcut.collect.run_command", side_effect=command):
            report = collect(self.project, limit=1)
        self.assertEqual(report["downloaded"], 1)
        self.assertEqual(len(list(self.project.inbox.glob("*.mp4"))), 1)
        responses.append(json.dumps({"cards": [{"video_id": "1234567890123456789"}]}))
        with patch("mixcut.collect.ROOT", self.root), patch("mixcut.collect.runtime_tool", return_value="socai.exe"), patch("mixcut.collect.run_command", side_effect=command):
            report = collect(self.project, limit=1)
        self.assertEqual(len(report["skipped"]), 1)

    def test_collector_reports_login_required_and_saves_diagnostics(self):
        payload = {"ok": False, "submit": {"state": {"login_required": True}}}
        error = CommandError(subprocess.CompletedProcess(["socai.exe"], 1, json.dumps(payload), "search failed"))
        with patch("mixcut.collect.runtime_tool", return_value="socai.exe"), patch("mixcut.collect.run_command", side_effect=error):
            with self.assertRaisesRegex(PipelineError, "扫码登录"):
                collect(self.project, limit=1)
        report = read_json(self.project.state / "collection/last_report.json")
        self.assertEqual(report["stage"], "search")

if __name__ == "__main__":
    unittest.main()
