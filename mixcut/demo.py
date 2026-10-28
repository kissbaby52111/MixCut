from __future__ import annotations
import math
from pathlib import Path
import struct
import wave
from .common import ROOT, Project, ROLES, read_json, write_json, run_command, runtime_tool
from .analyze import export_clip
from .planning import make_plans
from .render import render_pending

def make_demo(*, tail_seconds: float = 300, count: int = 1, width: int = 720, height: int = 1280, render: bool = True) -> dict:
    """Synthetic media only. Does not claim a live scrape or real AI classification."""
    from PIL import Image, ImageDraw, ImageFont
    base = ROOT / f"examples/demo_{width}x{height}_{tail_seconds:g}s"
    base.mkdir(parents=True, exist_ok=True)
    cfg = read_json(ROOT / "config.example.json")
    cfg.update({"demo": True, "workspace": "work", "workflow": "ai_pipeline"})
    cfg["mix"].update({"count": count, "max_source_uses_per_video": 2})
    cfg["render"].update({"width": width, "height": height, "tail_seconds": tail_seconds, "product_image": "test_product.png", "music": "test_music.wav", "preset": "ultrafast", "output_mode": "ffmpeg"})
    config_path = base / "config.json"
    write_json(config_path, cfg)
    project = Project(config_path)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.exists():
        font_path = Path("C:/Windows/Fonts/simhei.ttf")
    def font(size):
        return ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.load_default()
    product_image = base / "test_product.png"
    if not product_image.exists():
        image = Image.new("RGB", (720, 1280), "#141a32")
        d = ImageDraw.Draw(image)
        d.rounded_rectangle((220, 330, 500, 900), radius=60, fill="#b49b72", outline="#f7e7cc", width=6)
        d.rounded_rectangle((245, 260, 475, 410), radius=20, fill="#313a56")
        d.rounded_rectangle((245, 830, 475, 980), radius=20, fill="#313a56")
        d.text((70, 110), "双头染发膏", font=font(70), fill="#f5e5cc")
        d.text((70, 1010), "流程测试示意图", font=font(45), fill="#ffffff")
        d.text((70, 1090), "非真实商品图片", font=font(36), fill="#bbc8df")
        image.save(product_image)
    music = base / "test_music.wav"
    if not music.exists():
        rate = 48000
        with wave.open(str(music), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(rate)
            notes = [261.63, 329.63, 392.0, 329.63, 293.66, 349.23, 440, 349.23]
            samples = bytearray()
            for index in range(rate * 8):
                t = index / rate
                phase = t % 1.0
                env = min(1, phase*20) * max(0, 1-phase*.85)
                freq = notes[int(t) % len(notes)]
                value = int(7000 * env * (math.sin(2*math.pi*freq*t)*.65 + math.sin(2*math.pi*freq*2*t)*.15))
                samples.extend(struct.pack("<hh", value, value))
            out.writeframes(samples)
    colors = {"hooks": "#263959", "selling_points": "#31554c", "proof": "#5e4055", "conversion": "#5c4d31"}
    for role in cfg["mix"]["structure"]:
        for n in range(2):
            source = project.approved / f"synthetic_{role}_{n}.mp4"
            if not source.exists():
                card = Image.new("RGB", (720, 1280), colors[role])
                d = ImageDraw.Draw(card)
                d.text((60, 160), ROLES[role], font=font(80), fill="white")
                d.text((60, 320), f"测试片段 {n+1}", font=font(48), fill="#e4ddca")
                d.text((60, 890), "双头染发膏 · 流程测试", font=font(40), fill="white")
                d.text((60, 1030), "程序生成，非抖音采集", font=font(32), fill="#ddddee")
                card_path = base / f"{role}_{n}.png"
                card.save(card_path)
                run_command([runtime_tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-loop", "1", "-i", str(card_path), "-f", "lavfi", "-i", f"sine=frequency={300+n*130}:sample_rate=48000", "-t", "2.4", "-vf", f"scale={width}:{height},fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source)])
            export_clip(project, source, 0, 2.4, role, manual=True)
    existing = list(project.plans.glob("*.json"))
    if not existing:
        make_plans(project, count)
    if render:
        report = render_pending(project)
    else:
        report = {"message": "测试素材与计划已准备，尚未渲染"}
    report.update({"synthetic": True, "live_collection_tested": False, "real_ai_tested": False, "config": str(config_path)})
    write_json(base / "demo_report.json", report)
    return report
