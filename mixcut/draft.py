from __future__ import annotations
import os
from pathlib import Path
import re
import sys
import uuid
from .common import ROOT, Project, PipelineError, read_json, write_json, object_hash, file_hash, video_duration, now
from .tail_packaging import effective_render_config, PACKAGING_VERSION
from .jianying_assets import native_settings, validate_native, cloud_audio_material
from .auto_packaging import frozen_packaging
from .draft_timing import bounded_source_duration, shift_packaging_start

def inspect_draft(target) -> dict:
    """Report nesting depth, track count and unresolved local media of a draft folder."""
    root = Path(target).resolve()
    try:
        content = read_json(root / "draft_content.json")
    except (OSError, ValueError):
        content = None
    if not isinstance(content, dict):
        # The client rewrites opened drafts in an encrypted form; fall back to the
        # untouched sub-draft files, which is exactly where a nesting bug shows up.
        sub = root / "subdraft"
        reports = []
        for path in sorted(sub.glob("*/draft_content.json")) if sub.is_dir() else []:
            value = read_json(path, None)
            if isinstance(value, dict):
                reports.append({"folder": path.parent.name,
                    "has_nested_compound": bool(value.get("materials", {}).get("drafts")),
                    "tracks": len(value.get("tracks", []))})
        nested = [item["folder"] for item in reports if item["has_nested_compound"]]
        return {"path": str(root), "readable": False, "sub_drafts": len(reports),
                "sub_drafts_with_nested_compound": nested,
                "nested_compound_count": len(nested),
                "error": "draft_content.json 不存在或已被剪映改写加密；已改为检查 subdraft 子草稿。"}
    media, missing, tracks, nesting = [], [], 0, 0
    resources = set()
    def walk(value, level):
        nonlocal tracks, nesting
        nesting = max(nesting, level)
        tracks += len(value.get("tracks", []))
        for group in value.get("materials", {}).values():
            for material in group:
                if not isinstance(material, dict):
                    continue
                resource = material.get("resource_id")
                if isinstance(resource, str):
                    resources.add(resource)
                path = material.get("path")
                if isinstance(path, str) and re.match(r"^[A-Za-z]:[\\/]", path):
                    media.append(path)
        for material in value.get("materials", {}).get("drafts", []):
            child = material.get("draft") if isinstance(material, dict) else None
            if isinstance(child, dict):
                walk(child, level + 1)
    walk(content, 1)
    for path in dict.fromkeys(media):
        if not Path(path).is_file():
            missing.append(path)
    from .face_cover import SVIP_RESOURCE_IDS
    svip = sorted(resources & SVIP_RESOURCE_IDS)
    return {"path": str(root), "readable": True, "nesting_levels": nesting, "tracks": tracks,
            "media": len(set(media)), "missing_media": missing,
            "local_single_level": nesting <= 2, "ok": nesting <= 2 and not missing,
            "svip_materials": svip, "export_ready": nesting <= 2 and not missing,
            "export_warning": ("草稿使用了 SVIP 权益素材（拽酷墨镜）：账号已开通 SVIP 时可正常导出；"
                               "未开通时导出会报 10004，需关闭人物墨镜后重新生成。") if svip else None}


def export_draft(project: Project, plan_id: str, output: str = "") -> dict:
    if not re.fullmatch(r"[a-f0-9]{16}", plan_id):
        raise PipelineError("请输入组合计划的 16 位 ID。")
    plan = read_json(project.plans / f"{plan_id}.json")
    if not plan:
        raise PipelineError("找不到组合计划：" + plan_id)
    vendor = ROOT / "vendor/pyJianYingDraft"
    sys.path.insert(0, str(ROOT / ".runtime/packages"))
    sys.path.insert(0, str(vendor))
    os.environ["PYJIANYINGDRAFT_NO_UI"] = "1"
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise PipelineError(f"剪映草稿依赖未安装：{exc.name}。请运行 setup.bat，或安装 requirements-draft.txt 到 .runtime/packages。") from None
    import pymediainfo
    if not hasattr(pymediainfo, "MediaInfo"):
        raise PipelineError("当前受限进程无法读取草稿依赖。请在本机终端运行 run.bat draft；直接 MP4 渲染不受影响。")
    cfg = effective_render_config(plan)
    folder_mode = plan.get("mode") == "folder_mix"
    native = cfg.get("output_mode") == "jianying" and folder_mode
    options = validate_native(native_settings(cfg)) if native else None
    automatic = bool(native and options.get("selection_mode") == "auto")
    from .render import tail_images, prepared_photo
    images = tail_images(project, cfg)
    from .corner_overlay import frozen_corners
    corners = frozen_corners(project,cfg)
    packaging = frozen_packaging(plan, cfg) if automatic else None
    if automatic:
        from .local_music import verify_song
        for song in packaging["music"]:
            if song.get("path"):verify_song(song)
    local_music = cfg.get("music") if not native or options["music_source"] == "local" else ""
    music = project.asset(local_music) if local_music else None
    if (music and not music.is_file()) or (not music and (not folder_mode or (native and options["music_source"] == "local"))):
        raise PipelineError("请先配置存在的商品图和音乐文件。")
    prepared, adjustments = [], []
    for clip in plan["clips"]:
        path = Path(clip["path"])
        if not path.is_file() or file_hash(path) != clip["file_sha256"]:
            raise PipelineError("计划中的片段被移动或修改：" + str(path))
        try:
            material = draft.VideoMaterial(str(path))
        except (ValueError, OSError) as error:
            raise PipelineError(f"读取视频素材失败：{path.name}：{error}") from error
        requested = round(clip["duration"]*1_000_000)
        duration = bounded_source_duration(requested, material.duration, path.name)
        prepared.append((clip, material, duration))
        if duration != requested:
            adjustments.append({"path":str(path),"planned_us":requested,"source_us":material.duration,"used_us":duration})
    resolved_body_us = sum(duration for _,_,duration in prepared)
    packaging = shift_packaging_start(packaging, resolved_body_us)
    selected_root = options.get("draft_root", "") if options else ""
    root = Path(output).resolve() if output else project.asset(selected_root) if selected_root else project.path / "07_剪映草稿"
    root.mkdir(parents=True, exist_ok=True)
    safe_product = re.sub(r'[<>:"/\\|?*]', "_", project.keyword)
    # Bumping this string invalidates older drafts, whose timeline had tail-wide
    # effect tracks and a compound wrapper instead of segment effects + corners.
    asset_key = object_hash({"version": "draft-local-music-v10" if automatic and any(s.get("path") for s in packaging["music"]) else "draft-flat-v9", "cfg": cfg, "video_durations_us":[duration for _,_,duration in prepared], "images": images, "music": file_hash(music) if music else None})[:6]
    name = f"MixCut_{safe_product}_{plan_id}_{asset_key}"
    target = root / name
    if (target / "mixcut_manifest.json").exists():
        return read_json(target / "mixcut_manifest.json")
    if target.exists():
        backup = root / f"{name}_incomplete_{uuid.uuid4().hex[:6]}"
        if not target.resolve().is_relative_to(root.resolve()) or not backup.resolve().is_relative_to(root.resolve()):
            raise PipelineError("草稿路径超出输出目录。")
        target.rename(backup)
    script = draft.DraftFolder(str(root)).create_draft(name, int(cfg["width"]), int(cfg["height"]), fps=int(cfg["fps"]), allow_replace=False)
    # The upstream template contains a fixed ID; each output needs its own identity.
    script.content["id"] = str(uuid.uuid4()).upper()
    script.append_tracks([draft.TrackSpec(draft.TrackType.audio, "bgm"), draft.TrackSpec(draft.TrackType.video, "main")])
    cursor = 0
    meteor = cfg.get("meteor", {})
    scope = meteor.get("scope", "all")
    effect_enabled = not automatic and (bool(options["effect_enabled"]) if native else bool(meteor.get("enabled")))
    effect_key = options["effect_key"] if native else "流星雨"
    try:
        effect_type = draft.VideoSceneEffectType[effect_key] if effect_enabled else None
    except KeyError:
        raise PipelineError("当前草稿库未包含所选剪映特效：" + effect_key) from None
    for clip,material,duration in prepared:
        segment = draft.VideoSegment(material, draft.trange(cursor, duration),
            source_timerange=draft.trange(0,duration), volume=float(cfg.get("original_audio_volume", .7)))
        if not folder_mode and effect_enabled and scope in {"all", "body"}:
            segment.add_effect(effect_type)
        script.add_segment(segment, "main")
        cursor += duration
    tail_start = cursor
    for image in images:
        tail_duration = round(image["seconds"]*1_000_000)
        tail = draft.VideoSegment(str(prepared_photo(project, image)), draft.trange(cursor, tail_duration), volume=0)
        if effect_enabled and (folder_mode or scope in {"all", "tail"}):
            tail.add_effect(effect_type)
        if automatic:
            # Segment-scoped effects only cover the product images they are
            # attached to, so corner overlays on the upper tracks stay clean.
            for effect in packaging["effects"]:
                tail.add_effect(draft.VideoSceneEffectType[effect["key"]], params=effect.get("params"))
        script.add_segment(tail, "main")
        cursor += tail_duration
    total = cursor
    if automatic:
        for song in packaging["music"]:
            if song.get("path"):
                material = draft.AudioMaterial(str(project.asset(song["path"])))
            else:
                material = cloud_audio_material(draft, song)
            audio = draft.AudioSegment(material, draft.trange(song["start_us"], song["duration_us"]),
                source_timerange=draft.trange(song["source_start_us"],song["duration_us"]),
                volume=float(cfg.get("music_volume", .18)))
            script.add_segment(audio, "bgm")
        music_start = tail_start
    else:
        material = cloud_audio_material(draft, options["cloud_music"]) if native and options["music_source"] == "cloud" else draft.AudioMaterial(str(music)) if music else None
        music_duration = material.duration if material else total
        if music_duration <= 0:
            raise PipelineError("音乐时长无效。")
        position = tail_start if folder_mode or cfg.get("music_scope") == "tail" else 0
        music_start = position if material else None
        while material and position < total:
            length = min(music_duration, total-position)
            audio = draft.AudioSegment(material, draft.trange(position, length), volume=float(cfg.get("music_volume", .18)))
            script.add_segment(audio, "bgm")
            position += length
    script.save()
    # Publish both known draft filenames and unique metadata, retaining the old
    # filename for compatibility with the vendored library and its tests.
    content = read_json(target / "draft_content.json")
    from .face_cover import append_face_cover
    face_cover = append_face_cover(content, options or {}, prepared[0][2], total)
    if face_cover.get("applied"):
        write_json(target / "draft_content.json", content)
    corner_manifest={"enabled":False}
    if corners:
        from .corner_draft import merge_corners
        content,corner_manifest=merge_corners(draft,project,content,corners,cfg)
        write_json(target / "draft_content.json",content)
    write_json(target / "draft_info.json", content)
    meta = read_json(target / "draft_meta_info.json")
    meta.update(draft_id=content["id"], draft_name=name, draft_fold_path=str(target),
        draft_root_path=str(root), tm_duration=total)
    write_json(target / "draft_meta_info.json", meta)
    body_note = (f"前面视频保持原声；拽酷墨镜从 {face_cover['start_seconds']:g} 秒开始覆盖至结尾。"
                 if face_cover.get("applied") else "前面视频保持原声且不加包装。")
    if corners:
        body_note=body_note.replace("且不加包装","")+"四角产品图从开头到结尾贴边显示。"
    result = {"plan_id": plan_id, "draft_path": str(target), "duration": total/1_000_000,
        "planned_body_seconds": plan["body_seconds"], "duration_adjustments": adjustments,
        "body_seconds": tail_start/1_000_000, "tail_start_seconds": tail_start/1_000_000,
        "tail_seconds": (total-tail_start)/1_000_000,
        "music_start_seconds": music_start/1_000_000 if music_start is not None else None,
        "native_effect": effect_key if effect_enabled else None,
        "native_effects": packaging["effects"] if automatic else [],
        "music_playlist": packaging["music"] if automatic else [],
        "selection_mode": "auto" if automatic else "manual",
        "music_source":options["music_source"] if native else "local",
        "packaging_seed": packaging["seed"] if automatic else None,
        "face_cover": face_cover,
        "corner_overlay":corner_manifest,
        "cloud_music": options["cloud_music"] if native and not automatic and options["music_source"] == "cloud" else None,
        "packaging_scope": "tail" if folder_mode else scope, "packaging_version": PACKAGING_VERSION,
        "rendered": False, "time": now(), "note": "草稿已生成，尚未输出 MP4。"+body_note+"图片包装特效和配乐仅位于四张产品图。请在剪映中确认素材加载并导出。新版剪映自动批量导出尚未验证。"}
    write_json(target / "mixcut_manifest.json", result)
    if automatic:
        lines=["# 自动包装清单", "", "这是剪映草稿，尚未确认客户端资源加载及 MP4 导出。", "",
            f"前面视频：0–{tail_start/1_000_000:g} 秒，原声 100%。",
            f"四张产品图：{tail_start/1_000_000:g}–{total/1_000_000:g} 秒。", "",
            "五层动效同时覆盖上述四图区间：", ""]
        if face_cover.get("applied"):
            lines[6:6] = [f"人物墨镜：跳过第一段开头，从 {face_cover['start_seconds']:g} 秒到 {total/1_000_000:g} 秒。原声不变。", ""]
        lines.extend(f"{index}. {item['name']}（{item['category']}风格）" for index,item in enumerate(packaging["effects"],1))
        lines.extend(["", "风格为本地候选分组，不代表剪映官方完整分类。", "", "| 音乐 | 起点 / 秒 | 使用时长 / 秒 | 原曲时长 / 秒 |", "| --- | --- | --- | --- |"])
        lines.extend(f"| {item['name'].replace('|','／')} | {item['start_us']/1_000_000:g} | {item['duration_us']/1_000_000:g} | {item['seconds']:g} |" for item in packaging["music"])
        (target / "包装清单.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    if face_cover.get("applied"):
        warning = ("\n⚠ 该素材属于客户端 SVIP 权益：账号未开通时导出会报 10004"
                   "（material lost or no permission or corrupted），画面会被替换成默认图。\n"
                   if face_cover.get("export_blocked_without_svip") else "")
        (target/"人物墨镜说明.md").write_text(f"拽酷墨镜已开启；跳过第一个视频，从时间线 {face_cover['start_seconds']:g} 秒开始，到成片结束。\n前段原声不变，图片五效/音乐仍仅位于产品图。识别与跟随以剪映预览为准，可能只匹配部分人脸。\n本机客户端将该资源标为‘{face_cover['client_commercial_label']}’。{warning}",encoding="utf-8")
    if corners:
        lines=["# 四角产品图","",f"从 0 秒到 {total/1_000_000:g} 秒全程显示，包括开头和尾部。四角边距为 0，贴紧画面边缘。",
            "四张图片固定，保持比例；原有音乐、五层特效和人物墨镜在同一时间线，四角图在最上层视频轨。剪映实际加载与显示仍需预览验收。",""]
        lines.extend(f"- {i['position']}：{i['path']}" for i in corners["images"])
        (target/"四角产品图说明.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    if adjustments:
        print(f"已校正 {len(adjustments)} 段视频的毫秒级时长差异；产品图、音乐和特效已同步对齐。",flush=True)
    return result
