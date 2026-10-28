"""Configuration and previews for the prepared-folder workflow."""
from __future__ import annotations
import copy
import re
from .common import PipelineError, read_json, write_json
from .cli import project_lock
from .ui_service import UiService, number
from .folder_mix import settings, folder_status, create_default_folders
from .tail_packaging import folder_render_config
from .jianying_assets import native_settings, validate_native
from .auto_packaging import attach_auto_packaging
from .plan_batches import get_batch, scoped_paths
from .corner_overlay import settings as corner_settings,validate_options as validate_corners,image_pool,attach_corner_overlay


class FolderService(UiService):
    def __init__(self, config_path):
        super().__init__(config_path)
        create_default_folders(self.project)

    def save(self, values: dict) -> None:
        project = self.reload()
        config = copy.deepcopy(project.config)
        slots, ids = [], set()
        for slot in values["slots"]:
            label, folder = slot["label"].strip(), slot["folder"].strip()
            identity = slot["id"]
            if not label or not folder:
                raise PipelineError("每个视频段落都需要填写名称并选择文件夹。")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity) or identity in ids:
                raise PipelineError("视频段落编号不正确或重复。")
            if not project.asset(folder).is_dir():
                raise PipelineError(f"“{label}”文件夹不存在：{folder}")
            ids.add(identity)
            slots.append({"id":identity, "label":label, "folder":str(project.asset(folder))})
        if not 1 <= len(slots) <= 20:
            raise PipelineError("请保留 1–20 个视频段落。")
        image_folder = values["image_folder"].strip()
        if not image_folder or not project.asset(image_folder).is_dir():
            raise PipelineError("请选择存在的产品图文件夹。")
        image_seconds = number(values["image_seconds"], "每张图秒数", 1, 3600)
        count = number(values["count"], "出片数量", 1, 1000, integer=True)
        render = config["render"]
        output_mode = values.get("output_mode", render.get("output_mode", "ffmpeg"))
        if output_mode not in {"ffmpeg", "jianying"}:
            raise PipelineError("请选择本地 MP4 或剪映草稿输出。")
        render["output_mode"] = output_mode
        options = {**native_settings(render), **values.get("jianying", {})}
        if output_mode != "jianying" and options.get("face_cover_enabled"):
            raise PipelineError("原生‘拽酷墨镜’需要剪映草稿输出；选择剪映模式，或先关闭人物墨镜。")
        if output_mode == "jianying":
            options = validate_native(options)
            if options.get("selection_mode") == "auto" and options.get("music_source") == "local_index":
                from .local_music import local_music_pool
                local_music_pool(project, options)
        draft_root = str(options.get("draft_root", "")).strip()
        if draft_root and not project.asset(draft_root).is_dir():
            raise PipelineError("草稿保存目录不存在，请选择已有目录，或留空使用项目草稿目录。")
        options["draft_root"] = str(project.asset(draft_root)) if draft_root else ""
        render["jianying"] = options
        width = number(values["width"], "宽度", 64, 3840, integer=True)
        height = number(values["height"], "高度", 64, 3840, integer=True)
        if width % 2 or height % 2:
            raise PipelineError("视频宽高必须是偶数。")
        render.update(width=width, height=height, fps=number(values["fps"], "帧率", 1, 60, integer=True),
            music_volume=number(values["music_volume"], "音乐音量", 0, 1),
            original_audio_volume=number(values["original_audio_volume"], "原声音量", 0, 1))
        for key in ["music", "effect_video"]:
            value = values.get(key, "").strip()
            required = output_mode == "ffmpeg" or (key == "music" and options["music_source"] == "local")
            if required and value and not project.asset(value).is_file():
                raise PipelineError("所选音乐或特效文件不存在：" + value)
            render[key] = value
        if output_mode == "jianying" and options["music_source"] == "local" and not render.get("music"):
            raise PipelineError("请选择本地配乐文件，或更改配乐来源。")
        scope = values.get("scope", "all")
        if scope not in {"all", "body", "tail"}:
            raise PipelineError("请选择特效范围。")
        render.setdefault("meteor", {}).update(enabled=bool(values.get("meteor_enabled")), scope=scope,
            intensity=number(values["intensity"], "特效强度", 0, 1))
        config["render"] = folder_render_config(render)
        config["workflow"] = "folder_mix"
        config["folder_mix"] = {"slots":slots, "image_folder":str(project.asset(image_folder)), "image_count":4, "image_seconds":image_seconds}
        corners=validate_corners({**corner_settings(render),**values.get("corner_overlay",{})})
        if corners["folder"]:corners["folder"]=str(project.asset(corners["folder"]))
        render["corner_overlay"]=corners
        config["render"]["corner_overlay"]=corners
        if corners["enabled"]:
            # Validate against the submitted fallback folder, not the old config.
            previous_config=project.config;project.config=config
            try:image_pool(project,corners)
            finally:project.config=previous_config
        config["mix"]["count"] = count
        with project_lock(project):
            write_json(self.config_path, config)
        self.reload()

    def plan_rows(self, batch_id=None, *, all_history=False):
        rows = super().plan_rows()
        rows = [row for row in rows if read_json(self.project.plans / (row["id"]+".json"), {}).get("mode") == "folder_mix"]
        if all_history:
            return rows
        batch = get_batch(self.project,batch_id,required=False)
        if not batch:
            return []
        indexed = {row["id"]:row for row in rows}
        return [indexed[identity] for identity in batch["plan_ids"] if identity in indexed]

    def apply_render_to_pending(self, batch_id=None) -> int:
        project = self.reload()
        status = read_json(project.state / "render_status.json", {})
        seconds = float(settings(project)["image_seconds"])
        changed = []
        with project_lock(project):
            for path in scoped_paths(project,batch_id=batch_id):
                plan = read_json(path)
                if plan.get("mode") != "folder_mix" or status.get(plan["id"], {}).get("status") == "complete":
                    continue
                images = copy.deepcopy(plan["render"]["product_images"])
                previous_render=copy.deepcopy(plan["render"])
                for image in images:
                    image["seconds"] = seconds
                plan["render"] = folder_render_config(project.config["render"])
                plan["render"].update(product_images=images, product_image=images[0]["path"], tail_seconds=sum(image["seconds"] for image in images))
                attach_auto_packaging(project, plan)
                attach_corner_overlay(project,plan,previous=previous_render)
                changed.append((path,plan))
                status[plan["id"]] = {"status": "pending"}
            for path,plan in changed:
                write_json(path, plan)
            write_json(project.state / "render_status.json", status)
        return len(changed)
