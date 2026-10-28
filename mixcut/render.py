from __future__ import annotations
import math
import os
from pathlib import Path
import random
import subprocess
from .common import Project, PipelineError, ROOT, file_hash, object_hash, now, read_json, write_json, run_command, runtime_tool, media_info, video_duration, has_audio

def video_filter(width: int, height: int, fps: int) -> str:
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p"

def encoding(cfg: dict) -> list[str]:
    return ["-c:v", "libx264", "-preset", str(cfg.get("preset", "veryfast")), "-crf", str(cfg.get("crf", 23)), "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(cfg["fps"]), "-video_track_timescale", "90000", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k", "-movflags", "+faststart"]

def meteor_asset(project: Project, cfg: dict) -> Path | None:
    if not cfg.get("meteor", {}).get("enabled", False):
        return None
    custom = cfg.get("effect_video")
    if custom:
        path = project.asset(custom)
        if not path.is_file():
            raise PipelineError(f"特效视频不存在：{path}")
        return path
    settings = cfg["meteor"]
    key = object_hash({"preset": "original-meteor-v1", "seed": settings.get("seed", 101), "intensity": settings.get("intensity", .55), "fps": cfg["fps"]})[:16]
    target = project.state / "cache" / f"meteor_{key}.mov"
    if target.exists():
        return target
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        raise PipelineError("自动生成流星素材需要 Pillow：运行 setup.bat，或配置 render.effect_video 指向带透明通道的 MOV。") from None
    target.parent.mkdir(exist_ok=True)
    width, height, seconds = 360, 640, 4
    fps = int(cfg["fps"])
    rng = random.Random(settings.get("seed", 101))
    intensity = max(.0, min(1., float(settings.get("intensity", .55))))
    stars = [(rng.randrange(width), rng.randrange(height), rng.uniform(.4, 1.0), rng.random()*6.28) for _ in range(25)]
    meteors = [(rng.uniform(-width, width), rng.uniform(-height, height), rng.uniform(140, 260), rng.uniform(25, 65)) for _ in range(7)]
    temporary = target.with_suffix(".partial.mov")
    cmd = [runtime_tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(temporary)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile_log(project, "meteor") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err, creationflags=flags)
        try:
            for frame in range(seconds * fps):
                t = frame / fps
                image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                for x, y, light, phase in stars:
                    alpha = int(140 * intensity * light * (.55 + .45 * math.sin(t*2+phase)**2))
                    draw.ellipse((x, y, x+1, y+1), fill=(190, 215, 255, alpha))
                glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
                gd = ImageDraw.Draw(glow)
                for x, y, speed, length in meteors:
                    xx = ((x + speed*t) % (width + height)) - height*.4
                    yy = ((y + speed*t*1.45) % (height+length)) - length
                    for step in range(12):
                        f = step/12
                        alpha = int(230 * intensity * (1-f)**1.5)
                        sx, sy = xx-length*f*.6, yy-length*f
                        draw.line((sx, sy, sx-3, sy-5), fill=(190, 220, 255, alpha), width=2)
                    gd.line((xx, yy, xx-length*.6, yy-length), fill=(110, 175, 255, int(70*intensity)), width=6)
                image = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(3)), image)
                proc.stdin.write(image.tobytes())
            proc.stdin.close()
            proc.wait(timeout=120)
            if proc.returncode:
                raise PipelineError("生成流星效果失败，请查看 .state/logs。")
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise
    temporary.replace(target)
    return target

class tempfile_log:
    def __init__(self, project: Project, name: str):
        self.path = project.state / "logs" / f"{name}.log"
    def __enter__(self):
        self.path.parent.mkdir(exist_ok=True)
        self.file = self.path.open("wb")
        return self.file
    def __exit__(self, *args):
        self.file.close()

def render_part(project: Project, clips: list[dict], cfg: dict, target: Path, *, image: Path | None = None, effect: Path | None = None) -> None:
    if target.exists():
        return
    width, height, fps = int(cfg["width"]), int(cfg["height"]), int(cfg["fps"])
    if width < 64 or height < 64 or width % 2 or height % 2 or not 1 <= fps <= 60:
        raise PipelineError("输出尺寸必须是至少 64 的偶数，帧率为 1–60。")
    cmd = [runtime_tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", "-y"]
    filters = []
    if image:
        duration = float(cfg["tail_seconds"])
        if not 0 < duration <= 3600:
            raise PipelineError("商品图时长应在 0–3600 秒之间。")
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(image), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        filters += [f"[0:v]{video_filter(width,height,fps)},trim=duration={duration},setpts=PTS-STARTPTS[basev]", f"[1:a]atrim=duration={duration},asetpts=PTS-STARTPTS[basea]"]
        next_input = 2
    else:
        duration = sum(c["duration"] for c in clips)
        for clip in clips:
            cmd += ["-i", clip["path"]]
        next_input = len(clips)
        for index, clip in enumerate(clips):
            d = clip["duration"]
            filters.append(f"[{index}:v]{video_filter(width,height,fps)},trim=duration={d},setpts=PTS-STARTPTS[v{index}]")
            if has_audio(Path(clip["path"])):
                filters.append(f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={d},asetpts=PTS-STARTPTS,volume={float(cfg.get('original_audio_volume', .7))}[a{index}]")
            else:
                filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d},asetpts=PTS-STARTPTS[a{index}]")
        inputs = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
        filters.append(f"{inputs}concat=n={len(clips)}:v=1:a=1[basev][basea]")
    if effect:
        cmd += ["-stream_loop", "-1", "-i", str(effect)]
        filters.append(f"[{next_input}:v]scale={width}:{height},setsar=1,fps={fps},setpts=PTS-STARTPTS[fx]")
        filters.append("[basev][fx]overlay=shortest=1:format=auto,format=yuv420p[outv]")
        video_label = "[outv]"
    else:
        video_label = "[basev]"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".partial.mp4")
    cmd += ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters), "-map", video_label, "-map", "[basea]", "-t", str(duration), *encoding(cfg), str(temporary)]
    try:
        run_command(cmd, timeout=max(180, int(duration*15)))
        temporary.replace(target)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise

def check_output(path: Path, expected_seconds: float, width: int, height: int, *, deep: bool = False) -> dict:
    info = media_info(path)
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    duration = float(info.get("format", {}).get("duration", 0))
    if not videos or not audio or videos[0].get("width") != width or videos[0].get("height") != height or abs(duration-expected_seconds) > .5:
        raise PipelineError(f"成片检查失败：时长 {duration:.3f}s（预期 {expected_seconds:.3f}s），画幅/音轨不符。")
    if deep:
        run_command([runtime_tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"], timeout=max(180, int(duration*5)))
    return {"path": str(path), "duration": duration, "expected_duration": expected_seconds, "width": videos[0]["width"], "height": videos[0]["height"], "audio": True, "full_decode": deep, "bytes": path.stat().st_size, "sha256": file_hash(path), "time": now()}

def tail_images(project: Project, cfg: dict) -> list[dict]:
    items = cfg.get("product_images")
    if items is None:
        items = [{"path": cfg.get("product_image", ""), "seconds": cfg["tail_seconds"]}]
    if not items:
        raise PipelineError("计划中没有产品图。")
    result = []
    for item in items:
        path = project.asset(item.get("path", ""))
        seconds = float(item["seconds"])
        if not item.get("path") or not path.is_file():
            raise PipelineError("商品图文件不存在，请选择图片或重新生成计划：" + str(path))
        if not math.isfinite(seconds) or not 0 < seconds <= 3600:
            raise PipelineError("每张商品图时长应在 0–3600 秒之间。")
        digest = file_hash(path)
        if item.get("sha256") and digest != item["sha256"]:
            raise PipelineError("计划中的商品图被修改，请重新生成计划：" + str(path))
        result.append({"path": str(path), "seconds": seconds, "sha256": digest})
    if abs(sum(item["seconds"] for item in result)-float(cfg["tail_seconds"])) > .01:
        raise PipelineError("产品图时长合计与计划尾部时长不一致，请重新生成计划。")
    return result


def prepared_photo(project: Project, image: dict, max_side: int = 2160) -> Path:
    """Normalize a product image once and cache it as a real PNG.

    Product folders mix extensions and formats (a .jpeg that actually holds PNG
    data is common), and the desktop client's export path rejects such a file as
    "material lost or corrupted" (error 10004) while preview still shows it.
    Re-encoding to <sha>.png keeps the extension truthful and strips EXIF/CMYK.
    """
    digest = str(image.get("sha256") or file_hash(project.asset(image["path"])))
    target = project.state / "cache" / f"photo_{digest}.png"
    if target.is_file():
        return target
    from PIL import Image, ImageOps
    with Image.open(project.asset(image["path"])) as source:
        value = ImageOps.exif_transpose(source).convert("RGB")
    if max(value.size) > max_side:
        ratio = max_side / max(value.size)
        value = value.resize((max(1, round(value.width*ratio)), max(1, round(value.height*ratio))), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".partial.png")
    try:
        value.save(temporary, format="PNG")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def render_plan(project: Project, plan: dict) -> dict:
    from .tail_packaging import effective_render_config
    cfg = effective_render_config(plan)
    if cfg.get("jianying", {}).get("face_cover_enabled"):
        raise PipelineError("拽酷墨镜是剪映原生人物特效，请生成剪映草稿后导出 MP4。")
    if cfg.get("output_mode") == "jianying" and plan.get("mode") == "folder_mix":
        raise PipelineError("该计划使用剪映云端音乐/原生特效，请生成剪映草稿，再在剪映中导出 MP4。")
    for clip in plan["clips"]:
        path = Path(clip["path"])
        if not path.is_file() or file_hash(path) != clip["file_sha256"]:
            raise PipelineError("计划中的片段被移动或修改，请恢复素材或重新生成计划：" + str(path))
    images = tail_images(project, cfg)
    from .corner_overlay import frozen_corners,render_overlay
    corners=frozen_corners(project,cfg)
    music = project.asset(cfg["music"]) if cfg.get("music") else None
    if (music and not music.is_file()) or (not music and plan.get("mode") != "folder_mix"):
        raise PipelineError("请配置 render.music 音乐路径；可运行 demo 使用程序合成的测试音乐。")
    if music and not has_audio(music):
        raise PipelineError("背景音乐文件中没有可用音轨。")
    fingerprint = object_hash({"version": "renderer-v4", "signature": plan["signature"], "render": cfg, "images": images, "music": file_hash(music) if music else None, "effect": file_hash(project.asset(cfg["effect_video"])) if cfg.get("effect_video") else None})
    output = project.outputs / f"{plan['id']}_{fingerprint[:8]}.mp4"
    report_file = output.with_suffix(".report.json")
    expected = float(plan["body_seconds"]) + float(cfg["tail_seconds"])
    if output.exists() and report_file.exists():
        report = read_json(report_file)
        if report.get("sha256") == file_hash(output):
            return {**report, "reused": True}
    cache = project.state / "cache"
    cache.mkdir(exist_ok=True)
    effect = meteor_asset(project, cfg)
    scope = cfg.get("meteor", {}).get("scope", "all")
    if scope not in {"all", "body", "tail"}:
        raise PipelineError("meteor.scope 必须是 all、body 或 tail。")
    tails = []
    tail_cfg = {key: value for key,value in cfg.items() if key not in {"product_images", "product_image", "music", "music_volume", "original_audio_volume","corner_overlay","corner_overlay_plan"}}
    for index, image in enumerate(images, 1):
        part_cfg = {**tail_cfg, "tail_seconds": image["seconds"]}
        tail_key = object_hash({"cfg": part_cfg, "image": image["sha256"], "effect": file_hash(effect) if effect and scope in {"all", "tail"} else None, "v": 2})[:20]
        tail = cache / f"tail_{tail_key}.mp4"
        print(f"准备产品图 {index}/{len(images)}：{image['seconds']:g} 秒（已有缓存将复用）", flush=True)
        render_part(project, [], part_cfg, tail, image=Path(image["path"]), effect=effect if scope in {"all", "tail"} else None)
        tails.append(tail)
    body_cfg = {key:value for key,value in cfg.items() if key not in {"product_images", "product_image", "tail_seconds", "music", "music_volume","corner_overlay","corner_overlay_plan"}}
    body_key = object_hash({"cfg": body_cfg, "clips": [(clip["file_sha256"],clip["duration"]) for clip in plan["clips"]], "effect": file_hash(effect) if effect and scope in {"all", "body"} else None})[:20]
    body = cache / f"body_{body_key}.mp4"
    print(f"渲染主体 {plan['body_seconds']:.2f} 秒", flush=True)
    render_part(project, plan["clips"], cfg, body, effect=effect if scope in {"all", "body"} else None)
    # Frame-rate conversion can lengthen the body slightly. The concat demuxer
    # places the next file at this encoded duration, rather than the source sum.
    tail_start = video_duration(body)
    concat_file = cache / f"join_{fingerprint[:16]}.txt"
    def quoted(path: Path) -> str:
        return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"
    concat_file.write_text("".join(quoted(path) for path in [body,*tails]), encoding="utf-8")
    temporary = output.with_suffix(".partial.mp4")
    cmd = [runtime_tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file)]
    if music:
        delay = ""
        if cfg.get("music_scope") == "tail":
            # Delay by exact audio samples; BGM begins at the first product-image frame.
            delay = f",atrim=duration={cfg['tail_seconds']},adelay={math.ceil(tail_start*48000)}S:all=1"
        # Without normalization or a whole-video limiter, the original body audio keeps its gain.
        limiter = "" if plan.get("mode") == "folder_mix" else ",alimiter=limit=0.95"
        cmd += ["-stream_loop", "-1", "-i", str(music), "-filter_complex", f"[1:a]aresample=48000,volume={float(cfg.get('music_volume', .18))}{delay}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0{limiter},atrim=duration={expected}[outa]", "-map", "0:v:0", "-map", "[outa]", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy"]
    cmd += ["-t", str(expected), "-movflags", "+faststart", str(temporary)]
    try:
        run_command(cmd, timeout=max(180, int(expected*5)))
        if corners:
            cornered=output.with_suffix(".corners.partial.mp4")
            try:
                render_overlay(project,temporary,cornered,corners,cfg)
                cornered.replace(temporary)
            finally:
                if cornered.exists():cornered.unlink()
        report = check_output(temporary, expected, int(cfg["width"]), int(cfg["height"]), deep=True)
        temporary.replace(output)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    report.update({"path": str(output), "plan_id": plan["id"], "product": plan["product"], "body_seconds": plan["body_seconds"], "tail_start_seconds":tail_start, "tail_seconds": cfg["tail_seconds"], "tail_images": images, "music_start_seconds": math.ceil(tail_start*48000)/48000 if music and cfg.get("music_scope") == "tail" else 0 if music else None, "packaging_scope": "tail" if plan.get("mode") == "folder_mix" else scope, "packaging_version":cfg.get("packaging_version"), "render_fingerprint": fingerprint, "demo": bool(project.config.get("demo")), "effect": "custom_overlay" if effect and cfg.get("effect_video") else "original_meteor_overlay" if effect else "none"})
    report["corner_overlay"]={"enabled":bool(corners),"scope":"entire_output" if corners else None,
        "edge_margin_pixels":0,"images":corners["images"] if corners else []}
    write_json(report_file, report)
    return report

def render_pending(project: Project, plan_ids: list[str] | None = None, *, batch_id: str | None = None, all_plans: bool = False) -> dict:
    from .plan_batches import scoped_paths
    plans = [read_json(path) for path in scoped_paths(project,plan_ids,batch_id=batch_id,all_plans=all_plans)]
    mode = project.config.get("workflow", "ai_pipeline")
    plans = [plan for plan in plans if plan and plan.get("mode", "ai_pipeline") == mode]
    if not plans:
        raise PipelineError("没有组合计划，请先运行 plan。")
    status_file = project.state / "render_status.json"
    status = read_json(status_file, {})
    reports, failures = [], []
    for plan in plans:
        if plan.get("mode") == "folder_mix" and plan["render"].get("output_mode") == "jianying":
            print(f"生成剪映草稿 {plan['id']}", flush=True)
            status[plan["id"]] = {"status": "running", "time": now()}
            write_json(status_file, status)
            try:
                from .draft import export_draft
                result = export_draft(project, plan["id"])
                reports.append(result)
                status[plan["id"]] = {"status": "draft_ready", "path": result["draft_path"], "time": now()}
            except Exception as exc:
                failures.append({"plan_id": plan["id"], "error": str(exc)})
                status[plan["id"]] = {"status": "failed", "error": str(exc), "time": now()}
                print(f"草稿失败 {plan['id']}：{exc}",flush=True)
                import traceback
                log = project.state / "logs" / ("draft_"+plan["id"]+".log")
                log.parent.mkdir(exist_ok=True)
                log.write_text(traceback.format_exc(),encoding="utf-8")
            write_json(status_file, status)
            continue
        previous = status.get(plan["id"], {})
        if previous.get("status") == "complete" and previous.get("path"):
            completed = Path(previous["path"])
            saved_report = read_json(completed.with_suffix(".report.json"), {})
            if completed.is_file() and saved_report.get("sha256") == file_hash(completed):
                reports.append({**saved_report, "reused": True})
                print(f"复用已完成成片 {plan['id']}", flush=True)
                continue
        print(f"处理计划 {plan['id']}", flush=True)
        status[plan["id"]] = {"status": "running", "time": now()}
        write_json(status_file, status)
        try:
            result = render_plan(project, plan)
            reports.append(result)
            status[plan["id"]] = {"status": "complete", "path": result["path"], "time": now()}
        except PipelineError as exc:
            failures.append({"plan_id": plan["id"], "error": str(exc)})
            status[plan["id"]] = {"status": "failed", "error": str(exc), "time": now()}
        write_json(status_file, status)
    report = {"completed": len(reports), "failed": len(failures), "reports": reports, "errors": failures, "time": now()}
    write_json(project.state / "batch_report.json", report)
    if failures:
        raise PipelineError(f"{len(reports)} 条完成，{len(failures)} 条失败。修复后重跑 render 即可续跑；详情见 .state/batch_report.json。")
    return report
